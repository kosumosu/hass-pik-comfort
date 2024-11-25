import asyncio
import logging
from itertools import chain
from typing import Any, Dict, List, Mapping, Optional, Type, TypeVar, final

from homeassistant.components.sensor import SensorStateClass, SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ATTRIBUTION, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from custom_components.pik_comfort import DOMAIN
from custom_components.pik_comfort._base import (
    BasePikComfortEntity,
    async_setup_entry_for_platforms,
)
from custom_components.pik_comfort.api import (
    PaymentStatus,
    PikComfortAPI,
    PikComfortTicket,
    TicketStatus,
    PikComfortMeter,
    Tariff,
    MeterResourceType,

)
from custom_components.pik_comfort.const import ATTRIBUTION, DATA_ENTITIES

_TBasePikComfortEntity = TypeVar("_TBasePikComfortEntity", bound=BasePikComfortEntity)


async def async_process_update(
        hass: HomeAssistant, config_entry_id: str, async_add_entities
) -> None:
    api_object: PikComfortAPI = hass.data[DOMAIN][config_entry_id]

    new_entities = []
    remove_tasks = []

    # Retrieve entities with their types
    entities: Dict[
        Type[_TBasePikComfortEntity], List[_TBasePikComfortEntity]
    ] = hass.data[DATA_ENTITIES][config_entry_id]

    last_payment_entities = entities.get(PikComfortLastPaymentSensor, [])
    old_last_payment_entities = list(last_payment_entities)

    last_receipt_entities = entities.get(PikComfortLastReceiptSensor, [])
    old_last_receipt_entities = list(last_receipt_entities)

    ticket_entities = entities.get(PikComfortTicketSensor, [])
    old_ticket_entities = list(ticket_entities)

    tariff_entities = entities.get(PikComfortMeterTariffSensor, [])
    old_tariff_entities = list(tariff_entities)

    # Process accounts
    for account in api_object.info.accounts:
        # Process last payment per account
        account_key = (account.type, account.id)
        existing_entity = None

        for entity in last_payment_entities:
            if (entity.account_type, entity.account_id) == account_key:
                existing_entity = entity
                old_last_payment_entities.remove(entity)
                break

        if existing_entity is None:
            new_entities.append(
                PikComfortLastPaymentSensor(config_entry_id, *account_key)
            )
        else:
            existing_entity.async_schedule_update_ha_state(force_refresh=False)

        # Process last receipt per account
        # key is the same
        existing_entity = None
        for entity in last_receipt_entities:
            if (entity.account_type, entity.account_id) == account_key:
                existing_entity = entity
                old_last_receipt_entities.remove(entity)
                break

        if existing_entity is None:
            new_entities.append(
                PikComfortLastReceiptSensor(config_entry_id, *account_key)
            )
        else:
            existing_entity.async_schedule_update_ha_state(force_refresh=False)

        # Process tickets per account
        for ticket in account.tickets:
            ticket_key = (ticket.type, ticket.id)
            existing_entity = None

            for entity in ticket_entities:
                if (entity.ticket_type, entity.ticket_id) == ticket_key:
                    existing_entity = entity
                    old_ticket_entities.remove(existing_entity)
                    break

            if existing_entity is None:
                new_entities.append(
                    PikComfortTicketSensor(config_entry_id, *account_key, *ticket_key)
                )
            else:
                existing_entity.async_schedule_update_ha_state(force_refresh=False)

        # Process meters
        for meter in account.meters:
            for tariff in meter.tariffs:
                tariff_key = (meter.id, tariff.type)
                existing_entity = None
                for entity in tariff_entities:
                    if (entity.meter_id, entity.tariff_type) == tariff_key:
                        existing_entity = entity
                        old_tariff_entities.remove(existing_entity)
                        break
                if existing_entity is None:
                    new_entities.append(
                        PikComfortMeterTariffSensor(config_entry_id,
                                                    account.type, account.id, *tariff_key)
                    )
                else:
                    existing_entity.async_schedule_update_ha_state(
                        force_refresh=False)

    for entity in chain(
            old_ticket_entities,
            old_last_payment_entities,
            old_last_receipt_entities,
            old_tariff_entities,
    ):
        _LOGGER.debug(f"Scheduling entity {entity} for removal")
        remove_tasks.append(hass.async_create_task(entity.async_remove()))

    if new_entities:
        async_add_entities(new_entities, False)

    if remove_tasks:
        await asyncio.wait(remove_tasks, return_when=asyncio.ALL_COMPLETED)


async def async_setup_entry(
        hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities
) -> bool:
    config_entry_id = config_entry.entry_id

    async def _async_process_update() -> None:
        return await async_process_update(hass, config_entry_id, async_add_entities)

    await async_setup_entry_for_platforms(hass, config_entry, _async_process_update)

    return True


_LOGGER = logging.getLogger(__name__)


class PikComfortLastPaymentSensor(SensorEntity, BasePikComfortEntity):
    @property
    def icon(self) -> str:
        account_object = self.account_object

        if account_object is not None:
            last_payment = account_object.last_payment

            if last_payment is not None:
                if last_payment.status == PaymentStatus.ACCEPTED:
                    return "mdi:cash-check"
                elif last_payment.status == PaymentStatus.DECLINED:
                    return "mdi:cash-remove"

        return "mdi:cash"

    @property
    def name(self) -> str:
        account_object = self.account_object
        if account_object is None:
            account_id = self.account_id

        else:
            account_id = (
                    account_object.number
                    or account_object.premise_number
                    or account_object.id
            )

        return f"Last Payment {account_id}"

    @property
    def unique_id(self) -> str:
        return f"last_payment__{self.account_type}__{self.account_id}"

    @property
    def available(self) -> bool:
        account_object = self.account_object
        return bool(account_object and account_object.last_payment)

    @property
    def native_value(self) -> str:
        last_payment = self.account_object.last_payment
        if last_payment is None:
            return STATE_UNAVAILABLE

        return last_payment.status.name.lower()

    @property
    def device_class(self) -> str:
        return "pik_comfort_last_payment"

    @property
    def device_state_attributes(self) -> Optional[Mapping[str, Any]]:
        last_payment = self.account_object.last_payment

        if last_payment is None:
            return None

        account_object = self.account_object

        return {
            "amount": last_payment.amount,
            "status_id": last_payment.status_id,
            "check_url": last_payment.check_url,
            "bank_id": last_payment.bank_id,
            "timestamp": last_payment.timestamp.isoformat(),
            "payment_type": last_payment.payment_type,
            "source_name": last_payment.source_name,
            "id": last_payment.id,
            "type": last_payment.type,
            "account_id": account_object.id,
            "account_type": account_object.type,
            "account_number": account_object.number,
            ATTR_ATTRIBUTION: ATTRIBUTION,
        }


class PikComfortTicketSensor(SensorEntity, BasePikComfortEntity):
    def __init__(
            self,
            config_entry_id: str,
            account_type: str,
            account_id: str,
            ticket_type: str,
            ticket_id: str,
    ) -> None:
        SensorEntity.__init__(self)
        BasePikComfortEntity.__init__(self, config_entry_id, account_type, account_id)

        self.ticket_type: str = ticket_type
        self.ticket_id: str = ticket_id

    @property
    def _ticket_object(self) -> Optional[PikComfortTicket]:
        info = self.api_object.info
        if not info:
            return None

        key = (self.ticket_type, self.ticket_id)
        for account in info.accounts:
            for ticket in account.tickets:
                if (ticket.type, ticket.id) == key:
                    return ticket

        return None

    @property
    def unique_id(self) -> str:
        return f"ticket__{self.ticket_type}__{self.ticket_id}"

    @property
    def available(self) -> bool:
        return bool(self._ticket_object)

    @property
    def device_class(self) -> str:
        return "pik_comfort_ticket"

    @property
    def icon(self) -> str:
        ticket_object = self._ticket_object

        suffix = ""
        if ticket_object is not None:
            if ticket_object.is_viewed:
                suffix = "-outline"

            status = ticket_object.status
            if status == TicketStatus.RECEIVED:
                return "mdi:comment-processing" + suffix
            elif status == TicketStatus.DENIED:
                return "mdi:comment-remove" + suffix
            elif status == TicketStatus.PROCESSING:
                return "mdi:comment-arrow-right" + suffix
            elif status == TicketStatus.COMPLETED:
                return "mdi:comment-check" + suffix
            elif status == TicketStatus.UNKNOWN:
                return "mdi:comment-question" + suffix

        return "mdi:chat" + suffix

    @property
    def name(self) -> str:
        ticket_object = self._ticket_object
        ticket_id = self.ticket_id if ticket_object is None else ticket_object.number
        return f"Ticket №{ticket_id}"

    @property
    def native_value(self) -> str:
        ticket_object = self._ticket_object

        if ticket_object is None:
            return STATE_UNAVAILABLE

        return ticket_object.status.name.lower()

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        ticket_object = self._ticket_object

        if ticket_object is None:
            return {}

        account_object = self.account_object

        return {
            "number": ticket_object.number,
            "description": ticket_object.description,
            "created": ticket_object.created.isoformat(),
            "updated": ticket_object.updated.isoformat(),
            "last_status_changed": ticket_object.last_status_changed.isoformat(),
            "is_viewed": ticket_object.is_viewed,
            "is_commentable": ticket_object.is_commentable,
            "is_liked": ticket_object.is_liked,
            "comments_count": len(ticket_object.comments),
            "attachments_count": len(ticket_object.attachments),
            "id": ticket_object.id,
            "type": ticket_object.type,
            "account_id": account_object.id,
            "account_type": account_object.type,
            "account_number": account_object.number,
            ATTR_ATTRIBUTION: ATTRIBUTION,
        }


class PikComfortLastReceiptSensor(SensorEntity, BasePikComfortEntity):
    @property
    def icon(self) -> str:
        account_object = self.account_object

        if account_object is not None:
            last_receipt = account_object.last_receipt

            if last_receipt is not None:
                if (last_receipt.paid or 0.0) >= last_receipt.total:
                    return "mdi:text-box-check"

        return "mdi:text-box"


    @property
    def name(self) -> str:
        account_object = self.account_object
        if account_object is None:
            return f"Last Receipt {self.account_id}"

        account_id = (
                account_object.number or account_object.premise_number or account_object.id
        )
        return f"Last Receipt {account_id}"

    @property
    def unique_id(self) -> str:
        return f"last_receipt__{self.account_type}__{self.account_id}"

    @property
    def available(self) -> bool:
        account_object = self.account_object
        return bool(account_object and account_object.last_receipt)

    @property
    def native_value(self) -> float | None:
        last_receipt = self.account_object.last_receipt

        if last_receipt is None:
            return None

        return last_receipt.total - (last_receipt.paid or 0.0)

    @property
    def device_class(self) -> str:
        return SensorDeviceClass.MONETARY

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        last_receipt = self.account_object.last_receipt

        if last_receipt is None:
            return {}

        account_object = self.account_object

        return {
            "type": last_receipt.type,
            "period": last_receipt.period.isoformat(),
            "charge": last_receipt.charge,
            "corrections": last_receipt.corrections,
            "payment": last_receipt.payment,
            "initial": last_receipt.initial,
            "subsidy": last_receipt.subsidy,
            "total": last_receipt.total,
            "penalty": last_receipt.penalty,
            # "contents": last_receipt.contents,
            "paid": last_receipt.paid or 0.0,
            "debt": last_receipt.debt or 0.0,
            "account_id": account_object.id,
            "account_type": account_object.type,
            "account_number": account_object.number,
            ATTR_ATTRIBUTION: ATTRIBUTION,
        }


unitsMap = {
    "кВт⋅ч": {"unit": "kWh", "scale": 1.0},
    "м³": {"unit": "m³", "scale": 1.0},
    "Гкал": {"unit": "GJ", "scale": 4.1868},
}


class PikComfortMeterTariffSensor(SensorEntity, BasePikComfortEntity):
    tariff: Tariff
    meter: PikComfortMeter

    def __init__(
            self,
            config_entry_id: str,
            account_type: str,
            account_id: str,
            meter_id: str,
            tariff_type: str,
    ) -> None:
        SensorEntity.__init__(self)
        BasePikComfortEntity.__init__(self, config_entry_id, account_type, account_id)
        self.meter_id: str = meter_id
        self.tariff_type: str = tariff_type

    @property
    def meter_object(self) -> Optional[PikComfortMeter]:
        info = self.api_object.info

        if info is None:
            return None

        key = self.meter_id
        for account in info.accounts:
            for meter in account.meters:
                if meter.id == key:
                    return meter

        return None

    @property
    def tariff_object(self) -> Optional[Tariff]:
        key = self.tariff_type
        meter = self.meter_object
        for tariff in meter.tariffs:
            if tariff.type == key:
                return tariff
        return None

    @property
    @final
    def icon(self) -> str:
        rt = self.meter_object.resource_type
        if rt == MeterResourceType.COLD_WATER:
            return "mdi:water-outline"
        elif rt == MeterResourceType.HOT_WATER:
            return "mdi:water"
        elif rt == MeterResourceType.ELECTRICITY:
            return "mdi:meter-electric"
        elif rt == MeterResourceType.HEATING:
            return "mdi:heating-coil"
        return "mdi:counter"

    @property
    @final
    def native_unit_of_measurement(self) -> str:
        compatible_unit = unitsMap[self.meter_object.unit_name]
        if compatible_unit is None:
            return self.meter_object.unit_name
        return compatible_unit["unit"]

    @property
    @final
    def name(self) -> str:
        rt = self.meter_object.resource_type
        base_name = f"{rt.name.lower()} #{self.meter_object.factory_number}" if self.meter_object.factory_number is not None else rt.name.lower()
        if rt == MeterResourceType.ELECTRICITY:
            tt = self.tariff_object.tariff_type
            return f'{base_name} ({tt.name.lower().replace(" ", "")})'
        return base_name

    @property
    @final
    def unique_id(self) -> str:
        return f"meter_tariff__{self.meter_id}__{self.tariff_type}"

    @property
    @final
    def available(self) -> bool:
        return bool(self.tariff_object and self.tariff_object.value)

    @property
    @final
    def native_value(self) -> float:
        value = self.tariff_object.value
        if value is None:
            return STATE_UNAVAILABLE

        compatible_unit = unitsMap[self.meter_object.unit_name]
        if compatible_unit is None:
            return value

        return value * compatible_unit["scale"]

    @property
    @final
    def device_class(self) -> SensorDeviceClass:
        types = {
            MeterResourceType.COLD_WATER: SensorDeviceClass.WATER,
            MeterResourceType.HOT_WATER: SensorDeviceClass.WATER,
            MeterResourceType.ELECTRICITY: SensorDeviceClass.ENERGY,
            MeterResourceType.HEATING: SensorDeviceClass.ENERGY,
        }
        return types.get(self.meter_object.resource_type)

    @property
    @final
    def state_class(self):
        return SensorStateClass.TOTAL

    @property
    @final
    def extra_state_attributes(self) -> Mapping[str, Any]:
        tariff = self.tariff_object

        if tariff is None:
            return {}

        return {
            "type": tariff.type,
            "value": tariff.value,
            "user_value": tariff.value,
            "user_value_created": tariff.user_value_created,
            "user_value_updated": tariff.user_value_updated
        }

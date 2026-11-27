"""The AirPlay Bridge integration against a fake add-on, in a real Home Assistant."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.airplay_bridge.const import CONF_TOKEN, DOMAIN

from .conftest import COVER, TOKEN


def eid(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id, f"{platform} {unique_id} missing"
    return entity_id


async def setup(hass: HomeAssistant, addon) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={CONF_HOST: "127.0.0.1", CONF_PORT: addon.port, CONF_TOKEN: TOKEN},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await addon.wait_connected()
    return entry


async def test_user_flow(hass: HomeAssistant, addon) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: "127.0.0.1", CONF_PORT: addon.port, CONF_TOKEN: "wrong"},
    )
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: "127.0.0.1", CONF_PORT: addon.port, CONF_TOKEN: TOKEN},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_hassio_discovery(hass: HomeAssistant, addon) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HASSIO},
        data=HassioServiceInfo(
            config={"host": "127.0.0.1", "port": addon.port, "token": TOKEN},
            name="AirPlay 2 → Sendspin Bridge",
            slug="local_airplay2_sendspin_bridge",
            uuid="1234",
        ),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "hassio_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    # Rediscovery with a new token updates the existing entry
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HASSIO},
        data=HassioServiceInfo(
            config={"host": "127.0.0.1", "port": addon.port, "token": "new"},
            name="AirPlay 2 → Sendspin Bridge",
            slug="local_airplay2_sendspin_bridge",
            uuid="1234",
        ),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_TOKEN] == "new"
    await hass.async_block_till_done()  # the entry reloads with the new token
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_entities_and_push(hass: HomeAssistant, addon) -> None:
    entry = await setup(hass, addon)
    playing = eid(hass, "binary_sensor", "stube_echo_airplay")
    track = eid(hass, "sensor", "stube_echo_track")
    dropouts = eid(hass, "sensor", "stube_echo_dropouts")
    offset = eid(hass, "number", "stube_echo_offset")
    volume = eid(hass, "number", "stube_echo_airplay_volume")
    night = eid(hass, "switch", "stube_echo_night_mode")
    cover = eid(hass, "image", "stube_echo_cover")
    eid(hass, "button", "stube_echo_test_tone")
    eid(hass, "number", "stube_echo_trim")

    assert hass.states.get(playing).state == STATE_OFF
    assert float(hass.states.get(offset).state) == -225
    assert float(hass.states.get(volume).state) == 100  # default volume
    assert hass.states.get(night).state == STATE_ON

    # The add-on pushes a change: AirPlay starts with a track and a cover
    zone = addon.zones[0]
    zone.update(
        playing=True,
        track={"title": "Bohemian Rhapsody", "artist": "Queen", "album": "Opera"},
        cover="abc",
        dropouts=2,
    )
    await addon.push()
    await hass.async_block_till_done()
    assert hass.states.get(playing).state == STATE_ON
    state = hass.states.get(track)
    assert state.state == "Bohemian Rhapsody"
    assert state.attributes["artist"] == "Queen"
    assert hass.states.get(dropouts).state == "2"
    image = hass.data["image"].get_entity(cover)
    assert await image.async_image() == COVER

    # Commands go to the add-on
    await hass.services.async_call(
        "number", "set_value", {"entity_id": offset, "value": -230}, blocking=True
    )
    await hass.services.async_call(
        "number", "set_value", {"entity_id": volume, "value": 70}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": night}, blocking=True
    )
    await hass.async_block_till_done()
    assert ("set", 0, {"latency_offset_ms": -230}) in addon.calls
    assert ("set", 0, {"speaker_volume": 70}) in addon.calls
    assert ("set", 0, {"night_enabled": False}) in addon.calls
    assert float(hass.states.get(offset).state) == -230
    assert hass.states.get(night).state == STATE_OFF

    # Night mode switch is unavailable without a night volume
    zone["night_volume"] = None
    await addon.push()
    await hass.async_block_till_done()
    assert hass.states.get(night).state == STATE_UNAVAILABLE
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_test_tone(hass: HomeAssistant, addon) -> None:
    entry = await setup(hass, addon)
    button = eid(hass, "button", "stube_echo_test_tone")
    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True
    )
    assert ("test", 0, {}) in addon.calls
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_attached_to_echo_device(hass: HomeAssistant, addon) -> None:
    """The AirPlay entities sit on the Echo's own device."""
    echo_entry = MockConfigEntry(domain="hassmic")
    echo_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=echo_entry.entry_id,
        identifiers={("hassmic", "stube")},
        name="OG Stube Satellit",
    )
    er.async_get(hass).async_get_or_create(
        "sensor",
        "hassmic",
        "stube_token",
        suggested_object_id="stube_sendspin_pairing_token",
        device_id=device.id,
        config_entry=echo_entry,
    )
    entry = await setup(hass, addon)
    entity = er.async_get(hass).async_get(
        eid(hass, "binary_sensor", "stube_echo_airplay")
    )
    assert entity.device_id == device.id
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_new_speaker_added_live(hass: HomeAssistant, addon) -> None:
    entry = await setup(hass, addon)
    second = dict(addon.zones[0], index=1, name="Bad Echo", key="bad_echo", speakers=[])
    addon.zones.append(second)
    await addon.push()
    await hass.async_block_till_done()
    eid(hass, "binary_sensor", "bad_echo_airplay")
    assert await hass.config_entries.async_unload(entry.entry_id)

"""The select and text platforms start, restore and refuse values the way HA
would accept them.

Both are settings a device exposes (a log level, a mode, a name), and both have
limits HA enforces: a select's state must be on its menu, and a text's state
must fit its length and pattern. HA's TextEntity goes further and RAISES for a
value that does not fit, which would stop the entity being written at all. So a
starting or restored value outside the limits must be turned into something HA
accepts, never passed through.
"""

from homeassistant.core import State

from conftest import run

from custom_components.virtual.select import SELECT_SCHEMA, VirtualSelect
from custom_components.virtual.text import TEXT_SCHEMA, VirtualText

MENU = ["INFO", "DEBUG", "VERBOSE"]


def _select(**config):
    entity = VirtualSelect(SELECT_SCHEMA({"name": "Log Level", "options": MENU, **config}), False)
    entity._create_state(entity._config)
    return entity


def _text(**config):
    entity = VirtualText(TEXT_SCHEMA({"name": "Greeting", **config}), False)
    entity._create_state(entity._config)
    return entity


# --- select ---------------------------------------------------------------------

def test_select_starts_on_its_initial_option():
    entity = _select(initial_value="DEBUG")
    assert (entity.options, entity.current_option, entity.state) == (MENU, "DEBUG", "DEBUG")


def test_select_falls_back_to_the_first_option():
    assert _select(initial_value="NOPE").state == "INFO"
    assert _select().state == "INFO"


def test_select_restores_a_valid_option_only():
    entity = _select(initial_value="DEBUG")
    entity._restore_state(State("select.x", "VERBOSE"), entity._config)
    assert entity.state == "VERBOSE"
    entity._restore_state(State("select.x", "unavailable"), entity._config)
    assert entity.state == "DEBUG", "an off-menu restore goes back to the start option"


def test_select_option_changes_state():
    entity = _select()
    entity.async_write_ha_state = lambda: None
    run(entity.async_select_option("VERBOSE"))
    assert entity.state == "VERBOSE"


# --- text -------------------------------------------------------------------------

def test_text_carries_its_limits():
    entity = _text(min=2, max=8, pattern="[a-z]+", mode="password", initial_value="abc")
    assert (entity.min, entity.max, entity.pattern, entity.mode, entity.state) == \
        (2, 8, "[a-z]+", "password", "abc")


def test_text_defaults_are_has_own():
    entity = _text(initial_value="anything")
    assert (entity.min, entity.max, entity.mode, entity.state) == (0, 255, "text", "anything")


def test_text_that_breaks_its_limits_becomes_empty_not_an_error():
    """HA raises for these; the entity must hold no value instead."""
    assert _text(max=3, initial_value="too long").state is None
    assert _text(min=5, initial_value="abc").state is None
    assert _text(pattern="[0-9]+", initial_value="abc").state is None


def test_text_restores_and_drops_unknown():
    entity = _text(initial_value="start")
    entity._restore_state(State("text.x", "restored"), entity._config)
    assert entity.state == "restored"
    entity._restore_state(State("text.x", "unknown"), entity._config)
    assert entity.state is None


def test_text_set_value_changes_state():
    entity = _text()
    entity.async_write_ha_state = lambda: None
    run(entity.async_set_value("new"))
    assert entity.state == "new"

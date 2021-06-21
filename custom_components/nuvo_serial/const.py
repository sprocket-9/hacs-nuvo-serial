"""Constants for the Nuvo Multi-zone Amplifier Media Player component."""

DOMAIN = "nuvo_serial"

CONF_ZONES = "zones"
CONF_SOURCES = "sources"
ZONE = "zone"
SOURCE = "source"

CONF_SOURCE_1 = "source_1"
CONF_SOURCE_2 = "source_2"
CONF_SOURCE_3 = "source_3"
CONF_SOURCE_4 = "source_4"
CONF_SOURCE_5 = "source_5"
CONF_SOURCE_6 = "source_6"

CONF_VOLUME_STEP = "volume_step"
CONF_NOT_FIRST_RUN = "not_first_run"

CONTROL_EQ_BASS = "bass"
CONTROL_EQ_TREBLE = "treble"
CONTROL_EQ_BALANCE = "balance"
CONTROL_EQ_LOUDCMP = "loudcmp"
CONTROL_SOURCE_GAIN = "gain"

GROUP_MEMBER = 1
GROUP_NON_MEMBER = 0

SERVICE_SNAPSHOT = "snapshot"
SERVICE_RESTORE = "restore"
SERVICE_PAGE_ON = "page_on"
SERVICE_PAGE_OFF = "page_off"
SERVICE_PARTY_ON = "party_on"
SERVICE_PARTY_OFF = "party_off"
SERVICE_ALL_OFF = "all_off"

FIRST_RUN = "first_run"
NUVO_OBJECT = "nuvo_object"
UNDO_UPDATE_LISTENER = "update_update_listener"

DOMAIN_EVENT = "nuvo_serial_event"
EVENT_KEYPAD_PLAY_PAUSE = "keypad_play_pause"
EVENT_KEYPAD_PREV = "keypad_prev"
EVENT_KEYPAD_NEXT = "keypad_next"

KEYPAD_BUTTON_PLAYPAUSE = "PLAYPAUSE"
KEYPAD_BUTTON_PREV = "PREV"
KEYPAD_BUTTON_NEXT = "NEXT"

KEYPAD_BUTTON_TO_EVENT = {
    KEYPAD_BUTTON_PLAYPAUSE: EVENT_KEYPAD_PLAY_PAUSE,
    KEYPAD_BUTTON_PREV: EVENT_KEYPAD_PREV,
    KEYPAD_BUTTON_NEXT: EVENT_KEYPAD_NEXT,
}

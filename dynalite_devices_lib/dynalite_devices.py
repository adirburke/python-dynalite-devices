"""Class to create devices from a Dynalite hub."""

import asyncio

from .const import (
    CONF_ACT_LEVEL,
    CONF_ACTION,
    CONF_ACTION_CMD,
    CONF_ACTION_REPORT,
    CONF_ACTION_STOP,
    CONF_ACTIVE,
    CONF_ACTIVE_INIT,
    CONF_ACTIVE_OFF,
    CONF_ACTIVE_ON,
    CONF_ALL,
    CONF_AREA,
    CONF_AREA_OVERRIDE,
    CONF_AUTO_DISCOVER,
    CONF_CHANNEL,
    CONF_CHANNEL_CLASS,
    CONF_CHANNEL_COVER,
    CONF_CHANNEL_TYPE,
    CONF_CLOSE_PRESET,
    CONF_DEFAULT,
    CONF_DEVICE_CLASS,
    CONF_DURATION,
    CONF_FADE,
    CONF_HIDDEN_ENTITY,
    CONF_HOST,
    CONF_NAME,
    CONF_NO_DEFAULT,
    CONF_NONE,
    CONF_OPEN_PRESET,
    CONF_POLL_TIMER,
    CONF_PORT,
    CONF_PRESET,
    CONF_ROOM,
    CONF_ROOM_OFF,
    CONF_ROOM_ON,
    CONF_STOP_PRESET,
    CONF_TEMPLATE,
    CONF_TILT_TIME,
    CONF_TIME_COVER,
    CONF_TRGT_LEVEL,
    CONF_TRIGGER,
    DEFAULT_CHANNEL_TYPE,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_TEMPLATES,
    EVENT_CHANNEL,
    EVENT_CONNECTED,
    EVENT_DISCONNECTED,
    EVENT_PRESET,
    LOGGER,
)
from .cover import DynaliteTimeCoverDevice, DynaliteTimeCoverWithTiltDevice
from .dynalite import Dynalite
from .dynalitebase import DynaliteBaseDevice
from .light import DynaliteChannelLightDevice
from .switch import (
    DynaliteChannelSwitchDevice,
    DynaliteDualPresetSwitchDevice,
    DynalitePresetSwitchDevice,
)


class BridgeError(Exception):
    """For errors in the Dynalite bridge."""

    def __init__(self, message):
        """Initialize the exception."""
        self.message = message


class DynaliteDevices:
    """Manages a single Dynalite bridge."""

    def __init__(self, loop=None, newDeviceFunc=None, updateDeviceFunc=None):
        """Initialize the system."""
        self.active = None
        self.auto_discover = None
        self.loop = loop
        self.newDeviceFunc = newDeviceFunc
        self.updateDeviceFunc = updateDeviceFunc
        self.configured = False
        self.connected = False
        self.added_presets = {}
        self.added_channels = {}
        self.added_room_switches = {}
        self.added_time_covers = {}
        self.waiting_devices = []
        self.timer_active = False
        self.timer_callbacks = set()
        self.template = {}
        self.area = {}
        self._dynalite = Dynalite(broadcast_func=self.handleEvent,)

    async def async_setup(self):
        """Set up a Dynalite bridge based on host parameter in the config."""
        LOGGER.debug("bridge async_setup")
        if not self.loop:
            self.loop = asyncio.get_running_loop()
        # Run the dynalite object. Assumes self.configure() has been called
        self.loop.create_task(self._dynalite.connect(self.host, self.port))
        return True

    def configure(self, config):
        """Configure a Dynalite bridge based on host parameter in the config."""
        LOGGER.debug("bridge async_configure - %s", config)
        self.configured = False
        # insert the global values
        self.host = config[CONF_HOST]
        self.port = config.get(CONF_PORT, DEFAULT_PORT)
        self.name = config.get(CONF_NAME, f"{DEFAULT_NAME}-{self.host}")
        self.auto_discover = config.get(CONF_AUTO_DISCOVER, False)
        self.active = config.get(CONF_ACTIVE, CONF_ACTIVE_INIT)
        if self.active is True:
            self.active = CONF_ACTIVE_ON
        if self.active is False:
            self.active = CONF_ACTIVE_OFF
        self.poll_timer = config.get(CONF_POLL_TIMER, 1.0)
        self.default_fade = config.get(CONF_DEFAULT, {}).get(CONF_FADE, 0)
        # create the templates
        config_templates = config.get(CONF_TEMPLATE, {})
        for template in DEFAULT_TEMPLATES:
            self.template[template] = {}
            cur_template = config_templates.get(template, {})
            for conf in DEFAULT_TEMPLATES[template]:
                self.template[template][conf] = cur_template.get(
                    conf, DEFAULT_TEMPLATES[template][conf]
                )
        # create default presets
        config_presets = config.get(CONF_PRESET, {})
        default_presets = {}
        for preset in config_presets:
            cur_config = config_presets[preset]
            default_presets[int(preset)] = {
                CONF_NAME: cur_config.get(CONF_NAME, f"Preset {preset}"),
                CONF_FADE: cur_config.get(CONF_FADE, self.default_fade),
            }
        # create the areas with their channels and presets
        for area_val in config.get(CONF_AREA, {}):  # may be a string '123'
            area = int(area_val)
            area_config = config[CONF_AREA].get(area_val)
            self.area[area] = {
                CONF_NAME: area_config.get(CONF_NAME, f"Area {area}"),
                CONF_FADE: area_config.get(CONF_FADE, self.default_fade),
            }
            if CONF_TEMPLATE in area_config:
                self.area[area][CONF_TEMPLATE] = area_config[CONF_TEMPLATE]
            area_presets = {}
            area_channels = {}
            # User defined presets and channels first, then template presets, then defaults
            for preset in area_config.get(CONF_PRESET, {}):
                preset_config = area_config[CONF_PRESET][preset]
                area_presets[int(preset)] = {
                    CONF_NAME: preset_config.get(CONF_NAME, f"Preset {preset}"),
                    CONF_FADE: preset_config.get(CONF_FADE, self.area[area][CONF_FADE]),
                }
            for channel in area_config.get(CONF_CHANNEL, {}):
                channel_config = area_config[CONF_CHANNEL][channel]
                area_channels[int(channel)] = {
                    CONF_NAME: channel_config.get(CONF_NAME, f"Channel {channel}"),
                    CONF_FADE: channel_config.get(
                        CONF_FADE, self.area[area][CONF_FADE]
                    ),
                }
            # add the entities implicitly defined by templates
            template = area_config.get(CONF_TEMPLATE)
            if template:
                # Which type of value is a specific CONF
                conf_presets = [
                    CONF_ROOM_ON,
                    CONF_ROOM_OFF,
                    CONF_TRIGGER,
                    CONF_OPEN_PRESET,
                    CONF_CLOSE_PRESET,
                    CONF_STOP_PRESET,
                ]
                conf_values = [CONF_CHANNEL_CLASS, CONF_DURATION, CONF_TILT_TIME]
                conf_channels = [CONF_CHANNEL_COVER]

                for conf in self.template[template]:
                    conf_value = area_config.get(conf, self.template[template][conf])
                    if conf in conf_presets:
                        preset = int(conf_value)
                        if preset not in area_presets:
                            area_presets[preset] = {
                                CONF_NAME: f"Preset {preset}",
                                CONF_FADE: self.area[area][CONF_FADE],
                                # Trigger is the only exception
                                CONF_HIDDEN_ENTITY: (template != CONF_TRIGGER),
                            }
                        self.area[area][conf] = preset
                    elif conf in conf_channels:
                        channel = int(conf_value)
                        if channel not in area_channels:
                            area_channels[channel] = {
                                CONF_NAME: f"Channel {channel}",
                                CONF_FADE: self.area[area][CONF_FADE],
                                CONF_HIDDEN_ENTITY: True,
                            }
                        self.area[area][conf] = channel
                    else:
                        assert conf in conf_values
                        self.area[area][conf] = conf_value
            # Default presets
            if not area_config.get(CONF_NO_DEFAULT, False):
                for preset in default_presets:
                    if preset not in area_presets:
                        area_presets[preset] = default_presets[preset]
            self.area[area][CONF_PRESET] = area_presets
            self.area[area][CONF_CHANNEL] = area_channels
            # now register the channels and presets and ask for initial status if needed
            if self.active in [CONF_ACTIVE_INIT, CONF_ACTIVE_ON]:
                self._dynalite.request_area_preset(area)
            for channel in area_channels:
                self.create_channel_if_new(area, channel)
                if self.active in [CONF_ACTIVE_INIT, CONF_ACTIVE_ON]:
                    self._dynalite.request_channel_level(area, channel)
            for preset in area_presets:
                self.create_preset_if_new(area, preset)

        # register the rooms (switches on presets 1/4)
        # all the devices should be created for channels and presets
        self.register_rooms()
        # register the time covers
        self.register_time_covers()
        # callback for all devices
        if self.newDeviceFunc and self.waiting_devices:
            self.newDeviceFunc(self.waiting_devices)
            self.waiting_devices = []
        self.configured = True

    def register_rooms(self):
        """Register the room switches from two normal presets each."""
        for area, area_config in self.area.items():
            if area_config.get(CONF_TEMPLATE, "") == CONF_ROOM:
                if area in self.added_room_switches:
                    continue
                new_device = DynaliteDualPresetSwitchDevice(area, self)
                self.added_room_switches[area] = new_device
                new_device.set_device(
                    1, self.added_presets[area][area_config[CONF_ROOM_ON]]
                )
                new_device.set_device(
                    2, self.added_presets[area][area_config[CONF_ROOM_OFF]]
                )
                self.registerNewDevice("switch", new_device, False)

    def register_time_covers(self):
        """Register the time covers from three presets and a channel each."""
        for area, area_config in self.area.items():
            if area_config.get(CONF_TEMPLATE, "") == CONF_TIME_COVER:
                if area in self.added_time_covers:
                    continue
                if area_config[CONF_TILT_TIME] == 0:
                    new_device = DynaliteTimeCoverDevice(area, self)
                else:
                    new_device = DynaliteTimeCoverWithTiltDevice(area, self)
                self.added_time_covers[area] = new_device
                new_device.set_device(
                    1, self.added_presets[area][area_config[CONF_OPEN_PRESET]]
                )
                new_device.set_device(
                    2, self.added_presets[area][area_config[CONF_CLOSE_PRESET]]
                )
                new_device.set_device(
                    3, self.added_presets[area][area_config[CONF_STOP_PRESET]]
                )
                if area_config[CONF_CHANNEL_COVER] != 0:
                    channel_device = self.added_channels[area][
                        area_config[CONF_CHANNEL_COVER]
                    ]
                else:
                    channel_device = DynaliteBaseDevice(area, self)
                new_device.set_device(4, channel_device)
                self.registerNewDevice("cover", new_device, False)

    def registerNewDevice(self, category, device, hidden):
        """Register a new device and group all the ones prior to CONFIGURED event together."""
        # after initial configuration, every new device gets sent on its own. The initial ones are bunched together
        if not hidden:
            if self.configured:
                if self.newDeviceFunc:
                    self.newDeviceFunc([device])
            else:  # send all the devices together when configured
                self.waiting_devices.append(device)

    @property
    def available(self):
        """Return whether bridge is available."""
        return self.connected

    def updateDevice(self, device):
        """Update one or more devices."""
        if self.updateDeviceFunc:
            self.updateDeviceFunc(device)

    def handleEvent(self, event=None):
        """Handle all events."""
        LOGGER.debug("handleEvent - type=%s event=%s" % (event.eventType, event.data))
        if event.eventType == EVENT_CONNECTED:
            LOGGER.debug("Received CONNECTED message")
            self.connected = True
            self.updateDevice(CONF_ALL)
        elif event.eventType == EVENT_DISCONNECTED:
            LOGGER.debug("Received DISCONNECTED message")
            self.connected = False
            self.updateDevice(CONF_ALL)
        elif event.eventType == EVENT_PRESET:
            LOGGER.debug("Received PRESET message")
            self.handle_preset_selection(event)
        elif event.eventType == EVENT_CHANNEL:
            LOGGER.debug("Received PRESET message")
            self.handle_channel_change(event)
        else:
            LOGGER.debug(
                "Received unknown message type=%s data=%s", event.eventType, event.data
            )
        return

    def ensure_area(self, area):
        """Configure a default area if it is not yet in config."""
        if area not in self.area:
            LOGGER.debug(f"adding area {area} that is not in config")
            self.area[area] = {CONF_NAME: f"Area {area}", CONF_FADE: self.default_fade}

    def create_preset_if_new(self, area, preset):
        """Register a new preset."""
        LOGGER.debug("create_preset_if_new - area=%s preset=%s", area, preset)
        # if already configured, ignore
        if self.added_presets.get(area, {}).get(preset, False):
            return
        # if no autodiscover and not in config, ignore
        if not self.auto_discover:
            if not self.area.get(area, {}).get(CONF_PRESET, {}).get(preset, False):
                raise BridgeError(
                    f"No auto discovery and unknown preset (area {area} preset {preset}"
                )

        self.ensure_area(area)
        area_config = self.area[area]

        if CONF_PRESET not in area_config:
            area_config[CONF_PRESET] = {}
        if preset not in area_config[CONF_PRESET]:
            area_config[CONF_PRESET][preset] = {
                CONF_NAME: f"Preset {preset}",
                CONF_FADE: area_config[CONF_FADE],
            }
            # if the area is a template is a template, new presets should be hidden
            if area_config.get(CONF_TEMPLATE, False):
                area_config[CONF_PRESET][preset][CONF_HIDDEN_ENTITY] = True

        hidden = area_config[CONF_PRESET][preset].get(CONF_HIDDEN_ENTITY, False)
        new_device = DynalitePresetSwitchDevice(area, preset, self,)
        new_device.set_level(0)
        self.registerNewDevice("switch", new_device, hidden)
        if area not in self.added_presets:
            self.added_presets[area] = {}
        self.added_presets[area][preset] = new_device
        LOGGER.debug(
            "Creating Dynalite preset area=%s preset=%s hidden=%s", area, preset, hidden
        )

    def handle_preset_selection(self, event=None):
        """Change the selected preset."""
        LOGGER.debug("handle_preset_selection - event=%s", event.data)
        area = event.data[CONF_AREA]
        preset = event.data[CONF_PRESET]
        try:
            self.create_preset_if_new(area, preset)
        except BridgeError:
            # Unknown and no autodiscover
            return

        # Update all the preset devices
        for curPresetInArea in self.added_presets[area]:
            device = self.added_presets[area][curPresetInArea]
            if curPresetInArea == preset:
                device.set_level(1)
            else:
                device.set_level(0)
            self.updateDevice(device)

    def create_channel_if_new(self, area, channel):
        """Register a new channel."""
        LOGGER.debug("create_channel_if_new - area=%s, channel=%s", area, channel)
        if channel == CONF_ALL:
            return
        # if already configured, ignore
        if self.added_channels.get(area, {}).get(channel, False):
            return
        # if no autodiscover and not in config, ignore
        if not self.auto_discover:
            if not self.area.get(area, {}).get(CONF_CHANNEL, {}).get(channel, False):
                raise BridgeError(
                    f"No auto discovery and unknown channel (area {area} channel {channel}"
                )

        self.ensure_area(area)
        area_config = self.area[area]

        if CONF_CHANNEL not in area_config:
            area_config[CONF_CHANNEL] = {}
        if channel not in area_config[CONF_CHANNEL]:
            area_config[CONF_CHANNEL][channel] = {
                CONF_NAME: f"Channel {channel}",
                CONF_FADE: area_config[CONF_FADE],
            }
            # if the area is a template is a template, new channels should be hidden
            if area_config.get(CONF_TEMPLATE, False):
                area_config[CONF_CHANNEL][channel][CONF_HIDDEN_ENTITY] = True

        channel_config = area_config[CONF_CHANNEL][channel]
        LOGGER.debug("create_channel_if_new - channel_config=%s", channel_config)
        channel_type = channel_config.get(
            CONF_CHANNEL_TYPE, DEFAULT_CHANNEL_TYPE
        ).lower()
        hidden = channel_config.get(CONF_HIDDEN_ENTITY, False)

        if channel_type == "light":
            new_device = DynaliteChannelLightDevice(area, channel, self,)
            self.registerNewDevice("light", new_device, hidden)
        elif channel_type == "switch":
            new_device = DynaliteChannelSwitchDevice(area, channel, self,)
            self.registerNewDevice("switch", new_device, hidden)
        else:
            LOGGER.info("unknown chnanel type %s - ignoring", channel_type)
            return
        if area not in self.added_channels:
            self.added_channels[area] = {}
        self.added_channels[area][channel] = new_device
        LOGGER.debug("Creating Dynalite channel area=%s channel=%s", area, channel)

    def handle_channel_change(self, event=None):
        """Change the level of a channel."""
        LOGGER.debug("handle_channel_change - event=%s", event.data)
        LOGGER.debug("handle_channel_change called event = %s", event.msg)
        area = event.data[CONF_AREA]
        channel = event.data[CONF_CHANNEL]
        try:
            self.create_channel_if_new(area, channel)
        except BridgeError:
            # Unknown and no autodiscover
            return

        action = event.data[CONF_ACTION]
        if action == CONF_ACTION_REPORT:
            actual_level = (255 - event.data[CONF_ACT_LEVEL]) / 254
            target_level = (255 - event.data[CONF_TRGT_LEVEL]) / 254
            channelToSet = self.added_channels[area][channel]
            channelToSet.update_level(actual_level, target_level)
            self.updateDevice(channelToSet)
        elif action == CONF_ACTION_CMD:
            target_level = (255 - event.data[CONF_TRGT_LEVEL]) / 254
            # when there is only a "set channel level" command, assume that this is both the actual and the target
            actual_level = target_level
            channelToSet = self.added_channels[area][channel]
            channelToSet.update_level(actual_level, target_level)
            self.updateDevice(channelToSet)
        elif action == CONF_ACTION_STOP:
            if channel == CONF_ALL:
                for channel in self.added_channels.get(area, {}):
                    channelToSet = self.added_channels[area][channel]
                    channelToSet.stop_fade()
                    self.updateDevice(channelToSet)
            else:
                channelToSet = self.added_channels[area][channel]
                channelToSet.stop_fade()
                self.updateDevice(channelToSet)
        else:
            LOGGER.error("unknown action for channel change %s", action)

    def add_timer_listener(self, callback_func):
        """Add a listener to the timer and start if needed."""
        self.timer_callbacks.add(callback_func)
        if not self.timer_active:
            self.loop.call_later(1, self.timer_func)
            self.timer_active = True

    def remove_timer_listener(self, callback_func):
        """Remove a listener from a timer."""
        self.timer_callbacks.discard(callback_func)

    def timer_func(self):
        """Call callbacks and either schedule timer or stop."""
        if self.timer_callbacks:
            for callback in self.timer_callbacks:
                self.loop.call_soon(callback)
            self.loop.call_later(1, self.timer_func)
        else:
            self.timer_active = False

    def set_channel_level(self, area, channel, level, fade):
        """Set the level for a channel."""
        fade = self.area[area][CONF_CHANNEL][channel][CONF_FADE]
        self._dynalite.set_channel_level(area, channel, level, fade)

    def select_preset(self, area, preset):
        """Select a preset in an area."""
        fade = self.area[area][CONF_PRESET][preset][CONF_FADE]
        self._dynalite.select_preset(area, preset, fade)

    def get_channel_name(self, area, channel):
        """Return the name of a channel."""
        return f"{self.area[area][CONF_NAME]} {self.area[area][CONF_CHANNEL][channel][CONF_NAME]}"

    def get_channel_fade(self, area, channel):
        """Return the fade of a channel."""
        return self.area[area][CONF_CHANNEL][channel][CONF_FADE]

    def get_preset_name(self, area, preset):
        """Return the name of a preset."""
        return f"{self.area[area][CONF_NAME]} {self.area[area][CONF_PRESET][preset][CONF_NAME]}"

    def get_preset_fade(self, area, preset):
        """Return the fade of a preset."""
        return self.area[area][CONF_PRESET][preset][CONF_FADE]

    def get_multi_name(self, area):
        """Return the name of a multi-device."""
        return self.area[area][CONF_NAME]

    def get_device_class(self, area):
        """Return the class for a blind."""
        return self.area[area][CONF_DEVICE_CLASS]

    def getMasterArea(self, area):
        """Get the master area when combining entities from different Dynet areas to the same area."""
        if area not in self.area:
            LOGGER.error("getMasterArea - we should not get here")
            raise BridgeError("getMasterArea - area " + str(area) + "is not in config")
        area_config = self.area[area]
        master_area = area_config[CONF_NAME]
        if CONF_AREA_OVERRIDE in area_config:
            override_area = area_config[CONF_AREA_OVERRIDE]
            master_area = override_area if override_area.lower() != CONF_NONE else ""
        return master_area

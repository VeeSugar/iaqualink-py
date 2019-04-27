import logging
import time
import threading
import traceback

import aiohttp

from iaqualink.typing import Payload
from iaqualink.device import AqualinkDevice

MIN_SECS_TO_REFRESH = 15

LOGGER = logging.getLogger("aqualink")


class AqualinkSystem(object):
    def __init__(self, aqualink: "Aqualink", serial: str):
        self.aqualink = aqualink
        self.serial = serial
        self.devices = {}
        self.has_spa = None
        self.lock = threading.Lock()
        self.last_refresh = 0

    @property
    async def info(self) -> Payload:
        systems = await self.aqualink.get_systems()
        for x in systems:
            if x["serial_number"] == self.serial:
                return x
        raise Exception(f"System not found for serial {self.serial}.")

    async def get_devices(self):
        if not self.devices:
            await self.update()
        return self.devices

    async def update(self) -> None:
        self.lock.acquire()

        # Be nice to Aqualink servers since we rely on polling.
        now = int(time.time())
        delta = now - self.last_refresh
        if delta < MIN_SECS_TO_REFRESH:
            LOGGER.debug(f"Only {delta}s since last refresh.")
            self.lock.release()
            return

        try:
            r1 = await self.aqualink.send_home_screen_request(self.serial)
            r2 = await self.aqualink.send_devices_screen_request(self.serial)
            await self._parse_home_response(r1)
            await self._parse_devices_response(r2)
        except Exception as e:  # pylint: disable=W0703
            LOGGER.error(f"Unhandled exception: {e}")
            for line in traceback.format_exc().split("\n"):
                LOGGER.error(line)
        else:
            self.last_refresh = int(time.time())

        # Keep track of the presence of the spa so we know whether temp1 is
        # for the spa or the pool. This is pretty ugly.
        if "spa_set_point" in self.devices:
            self.has_spa = True
        else:
            self.has_spa = False

        self.lock.release()

    async def _parse_home_response(self, response: aiohttp.ClientResponse) -> None:
        data = await response.json()

        if data["home_screen"][0]["status"] == "Offline":
            LOGGER.warning(f"Status for system {self.serial} is Offline.")
            return

        # Make the data a bit flatter.
        devices = {}
        for x in data["home_screen"][4:]:
            name = list(x.keys())[0]
            state = list(x.values())[0]
            attrs = {"name": name, "state": state}
            devices.update({name: attrs})

        for k, v in devices.items():
            if k in self.devices:
                self.devices[k].data["state"] = v["state"]
            else:
                self.devices[k] = AqualinkDevice.from_data(self, v)

    async def _parse_devices_response(self, response: aiohttp.ClientResponse) -> None:
        data = await response.json()

        if data["devices_screen"][0]["status"] == "Offline":
            LOGGER.warning(f"Status for system {self.serial} is Offline.")
            return

        # Make the data a bit flatter.
        devices = {}
        for i, x in enumerate(data["devices_screen"][3:], 1):
            attrs = {"aux": f"{i}", "name": list(x.keys())[0]}
            for y in list(x.values())[0]:
                attrs.update(y)
            devices.update({f"aux_{i}": attrs})

        for k, v in devices.items():
            if k in self.devices:
                self.devices[k].data["state"] = v["state"]
            else:
                self.devices[k] = AqualinkDevice.from_data(self, v)

    async def set_pump(self, command: str) -> None:
        r = await self.aqualink.set_pump(self.serial, command)
        await self._parse_home_response(r)

    async def set_heater(self, command: str) -> None:
        r = await self.aqualink.set_heater(self.serial, command)
        await self._parse_home_response(r)

    async def set_temps(self, temps: Payload) -> None:
        r = await self.aqualink.set_temps(self.serial, temps)
        await self._parse_home_response(r)

    async def set_aux(self, aux: str) -> None:
        r = await self.aqualink.set_aux(self.serial, aux)
        await self._parse_devices_response(r)

    async def set_light(self, data: Payload) -> None:
        r = await self.aqualink.set_light(self.serial, data)
        await self._parse_devices_response(r)
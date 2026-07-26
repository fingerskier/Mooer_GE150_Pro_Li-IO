"""USB HID connection to the Mooer GE150 Pro Li.

Supports both ``hidapi`` (preferred) and ``pyusb`` backends.
The device presents as a USB composite device (audio + MIDI + HID). The
editor protocol runs on the HID interface, which the USB configuration
descriptor in ``log/various_tests.pcapng`` places at interface 5 with
64-byte interrupt endpoints 0x85 (IN) and 0x05 (OUT).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ..protocol.framing import (
    Frame,
    parse_message,
    message_total_size,
    HID_REPORT_SIZE,
)

logger = logging.getLogger(__name__)

VENDOR_ID = 0x34DB
PRODUCT_ID = 0x000F
HID_INTERFACE = 5
EP_IN = 0x85
EP_OUT = 0x05
READ_TIMEOUT_MS = 1000

#: The IDs this repo targeted before the USB capture. They belong to the
#: older STM32-based Mooer units (GE200 and relatives), not to this pedal.
LEGACY_VENDOR_ID = 0x0483
LEGACY_PRODUCT_ID = 0x5703


@dataclass
class DeviceInfo:
    """Basic device identification from USB descriptors."""

    vendor_id: int = VENDOR_ID
    product_id: int = PRODUCT_ID
    manufacturer: str = ""
    product: str = ""
    path: str = ""


class USBConnection:
    """Manages the USB HID connection to the Mooer pedal.

    Usage::

        conn = USBConnection()
        conn.open()
        conn.write(frame_bytes)
        response = conn.read()
        conn.close()
    """

    def __init__(
        self,
        vendor_id: int = VENDOR_ID,
        product_id: int = PRODUCT_ID,
    ) -> None:
        self._vendor_id = vendor_id
        self._product_id = product_id
        self._device = None
        self._backend: str = ""
        self._connected = False
        self._device_info = DeviceInfo(vendor_id=vendor_id, product_id=product_id)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_info

    def open(self) -> DeviceInfo:
        """Open a connection to the pedal, trying hidapi first, then pyusb.

        Returns:
            DeviceInfo with USB descriptor information.

        Raises:
            ConnectionError: If the device cannot be found or opened.
        """
        try:
            return self._open_hidapi()
        except Exception as e:
            logger.debug("hidapi backend failed: %s, trying pyusb", e)

        try:
            return self._open_pyusb()
        except Exception as e:
            raise ConnectionError(
                f"Could not connect to Mooer device "
                f"({self._vendor_id:#06x}:{self._product_id:#06x}). "
                f"Ensure the device is connected and you have permissions. "
                f"Last error: {e}"
            ) from e

    def _open_hidapi(self) -> DeviceInfo:
        """Open using the hidapi library."""
        import hid

        device = hid.device()
        device.open(self._vendor_id, self._product_id)
        device.set_nonblocking(False)

        self._device = device
        self._backend = "hidapi"
        self._connected = True

        info = device.get_device_info() if hasattr(device, 'get_device_info') else {}
        self._device_info = DeviceInfo(
            vendor_id=self._vendor_id,
            product_id=self._product_id,
            manufacturer=getattr(info, 'manufacturer_string', '') or device.get_manufacturer_string() or '',
            product=getattr(info, 'product_string', '') or device.get_product_string() or '',
        )

        logger.info(
            "Connected via hidapi: %s %s",
            self._device_info.manufacturer,
            self._device_info.product,
        )
        return self._device_info

    def _open_pyusb(self) -> DeviceInfo:
        """Open using pyusb + libusb."""
        import usb.core
        import usb.util

        dev = usb.core.find(idVendor=self._vendor_id, idProduct=self._product_id)
        if dev is None:
            raise ConnectionError("Device not found via pyusb")

        # Detach kernel driver if needed
        if dev.is_kernel_driver_active(HID_INTERFACE):
            dev.detach_kernel_driver(HID_INTERFACE)

        usb.util.claim_interface(dev, HID_INTERFACE)

        self._device = dev
        self._backend = "pyusb"
        self._connected = True
        self._device_info = DeviceInfo(
            vendor_id=self._vendor_id,
            product_id=self._product_id,
            manufacturer=usb.util.get_string(dev, dev.iManufacturer) or "",
            product=usb.util.get_string(dev, dev.iProduct) or "",
        )

        logger.info(
            "Connected via pyusb: %s %s",
            self._device_info.manufacturer,
            self._device_info.product,
        )
        return self._device_info

    def close(self) -> None:
        """Close the USB connection."""
        if not self._connected:
            return

        try:
            if self._backend == "hidapi":
                self._device.close()
            elif self._backend == "pyusb":
                import usb.util
                usb.util.release_interface(self._device, HID_INTERFACE)
        except Exception as e:
            logger.warning("Error closing device: %s", e)
        finally:
            self._device = None
            self._connected = False
            logger.info("Disconnected")

    def write(self, data: bytes) -> int:
        """Write a 64-byte HID report to the device.

        Args:
            data: A 64-byte HID report.

        Returns:
            Number of bytes written.

        Raises:
            ConnectionError: If not connected.
            IOError: If the write fails.
        """
        if not self._connected:
            raise ConnectionError("Not connected to device")

        if len(data) != HID_REPORT_SIZE:
            raise ValueError(
                f"HID report must be {HID_REPORT_SIZE} bytes, got {len(data)}"
            )

        if self._backend == "hidapi":
            return self._device.write(data)
        elif self._backend == "pyusb":
            return self._device.write(EP_OUT, data, timeout=READ_TIMEOUT_MS)
        else:
            raise RuntimeError(f"Unknown backend: {self._backend}")

    def read(self, timeout_ms: int = READ_TIMEOUT_MS) -> bytes | None:
        """Read a 64-byte HID report from the device.

        Args:
            timeout_ms: Read timeout in milliseconds.

        Returns:
            A 64-byte report, or None if the read timed out.

        Raises:
            ConnectionError: If not connected.
        """
        if not self._connected:
            raise ConnectionError("Not connected to device")

        try:
            if self._backend == "hidapi":
                data = self._device.read(HID_REPORT_SIZE, timeout_ms)
                if data:
                    return bytes(data)
                return None
            elif self._backend == "pyusb":
                data = self._device.read(EP_IN, HID_REPORT_SIZE, timeout=timeout_ms)
                return bytes(data)
        except Exception as e:
            logger.debug("Read error: %s", e)
            return None

    def read_message(self, timeout_ms: int = READ_TIMEOUT_MS) -> Frame | None:
        """Read one complete protocol message, reassembling chunked reports.

        Responses larger than one 64-byte HID report (e.g. the 512-byte
        preset data) arrive split across multiple reports. Each report
        carries a 1-byte length prefix; the first report's message header
        (preamble + size) tells us how many total bytes to expect.

        Args:
            timeout_ms: Timeout for each individual report read.

        Returns:
            The parsed Frame, or None on timeout, bad preamble, or
            checksum failure.
        """
        report = self.read(timeout_ms)
        if report is None:
            return None

        chunk_size = report[0]
        assembled = bytes(report[1 : 1 + chunk_size])

        total = message_total_size(assembled)
        if total is None:
            return None

        while len(assembled) < total:
            report = self.read(timeout_ms)
            if report is None:
                logger.debug(
                    "Timed out mid-message: %d of %d bytes assembled",
                    len(assembled), total,
                )
                return None
            chunk_size = report[0]
            assembled += bytes(report[1 : 1 + chunk_size])

        return parse_message(assembled)

    def send_and_receive(
        self,
        data: bytes,
        timeout_ms: int = READ_TIMEOUT_MS,
    ) -> Frame | None:
        """Send a command and read the response, parsing it into a Frame.

        Args:
            data: A 64-byte HID report to send.
            timeout_ms: Response read timeout.

        Returns:
            Parsed Frame, or None if no valid response was received.
        """
        self.write(data)
        return self.read_message(timeout_ms)

    def send_chunked_and_receive(
        self,
        frames: list[bytes],
        timeout_ms: int = READ_TIMEOUT_MS,
        inter_frame_delay: float = 0.01,
    ) -> Frame | None:
        """Send multiple HID reports (chunked message) and read the response.

        Args:
            frames: List of 64-byte HID reports.
            timeout_ms: Response read timeout after last frame.
            inter_frame_delay: Delay in seconds between frames.

        Returns:
            Parsed Frame, or None if no valid response.
        """
        for i, frame in enumerate(frames):
            self.write(frame)
            if i < len(frames) - 1:
                time.sleep(inter_frame_delay)

        return self.read_message(timeout_ms)

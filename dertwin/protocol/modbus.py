import asyncio
import logging
from typing import Optional

from pymodbus.datastore import ModbusServerContext, ModbusSequentialDataBlock, ModbusDeviceContext
from pymodbus.server import ModbusTcpServer, ModbusSerialServer

logger = logging.getLogger(__name__)

# Full 16-bit Modbus address space: 0x0000–0xFFFF
_MODBUS_REGISTER_COUNT = 65536


def create_device_context() -> ModbusDeviceContext:
    di_block = ModbusSequentialDataBlock(0, [0] * _MODBUS_REGISTER_COUNT)
    co_block = ModbusSequentialDataBlock(0, [0] * _MODBUS_REGISTER_COUNT)
    ir_block = ModbusSequentialDataBlock(0, [0] * _MODBUS_REGISTER_COUNT)
    hr_block = ModbusSequentialDataBlock(0, [0] * _MODBUS_REGISTER_COUNT)

    return ModbusDeviceContext(
        di=di_block,
        co=co_block,
        ir=ir_block,
        hr=hr_block,
    )


# ==========================================================
# MODBUS TCP
# ==========================================================

class ModbusTCPSimulator:
    """
    Asynchronous Modbus TCP server for a single simulated device.

    Uses a TCP socket as the transport layer. Holds a direct reference
    to the underlying ``ModbusTcpServer`` so ``shutdown()`` can close
    its listening socket cleanly — important for dynamic add/remove
    cycles where the same port may be re-bound shortly after release.
    """

    def __init__(self, address: str, port: int, unit_id: int):
        self.address = address
        self.port = port
        self.unit_id = unit_id
        device_context = create_device_context()

        self.context = ModbusServerContext(
            devices={unit_id: device_context},
            single=False,
        )

        self._task: Optional[asyncio.Task] = None
        self._server: Optional[ModbusTcpServer] = None

    # ---------------------------------------------------------

    async def run_server(self):
        """Start the asynchronous Modbus TCP server.

        Manages the server lifecycle directly (instead of using
        ``StartAsyncTcpServer``) so ``shutdown()`` can close the
        listening socket explicitly via ``server.server_close()`` and
        ``server.shutdown()``. The previous wrapper-based approach
        leaked listening sockets across add/remove cycles.
        """
        logger.info(
            "Starting Modbus TCP server | %s:%s | unit=%s",
            self.address,
            self.port,
            self.unit_id,
        )

        self._server = ModbusTcpServer(
            context=self.context,
            address=(self.address, self.port),
        )

        async def _serve():
            try:
                await self._server.serve_forever()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Modbus TCP server crashed | %s:%s", self.address, self.port,
                )
                raise

        self._task = asyncio.create_task(_serve())

    # ---------------------------------------------------------

    async def shutdown(self):
        """Stop the TCP server and release its listening socket.

        Calls ``server.shutdown()`` explicitly to close the
        ``asyncio.Server`` (and its listening socket) before cancelling
        the task. Without this, the kernel keeps the port bound for
        tens of seconds even after the task is gone.
        """
        if self._task is None:
            return

        logger.info(
            "Stopping Modbus TCP server | %s:%s",
            self.address,
            self.port,
        )

        # Close the listening socket and any client connections.
        if self._server is not None:
            try:
                await self._server.shutdown()
            except Exception:
                logger.exception(
                    "Error during pymodbus server shutdown | %s:%s",
                    self.address, self.port,
                )

        # Cancel the serve task. With the server already closed,
        # serve_forever() should return cleanly; cancel is belt-and-braces.
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("Modbus TCP server task ended with error: %s", e)

        self._task = None
        self._server = None


# ==========================================================
# MODBUS RTU
# ==========================================================

class ModbusRTUSimulator:
    """
    Asynchronous Modbus RTU server for a single simulated device.

    Uses a serial port (physical or virtual) as the transport layer.
    Like the TCP simulator, holds a direct reference to the underlying
    server so ``shutdown()`` can close the serial port cleanly.
    """

    def __init__(
        self,
        port: str,
        unit_id: int,
        baudrate: int = 9600,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: int = 1,
        timeout: float = 1.0,
    ):
        self.port = port
        self.unit_id = unit_id
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.timeout = timeout

        device_context = create_device_context()

        self.context = ModbusServerContext(
            devices={unit_id: device_context},
            single=False,
        )

        self._task: Optional[asyncio.Task] = None
        self._server: Optional[ModbusSerialServer] = None

    # ---------------------------------------------------------

    async def run_server(self):
        """Start the asynchronous Modbus RTU serial server."""
        logger.info(
            "Starting Modbus RTU server | port=%s | baudrate=%s | unit=%s",
            self.port,
            self.baudrate,
            self.unit_id,
        )

        try:
            self._server = ModbusSerialServer(
                context=self.context,
                port=self.port,
                baudrate=self.baudrate,
                bytesize=self.bytesize,
                parity=self.parity,
                stopbits=self.stopbits,
                timeout=self.timeout,
            )
        except Exception as e:
            logger.warning(
                "Failed to construct Modbus RTU server | port=%s | error=%s",
                self.port, e,
            )
            return

        async def _serve():
            try:
                await self._server.serve_forever()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Modbus RTU server crashed | port=%s", self.port,
                )
                raise

        self._task = asyncio.create_task(_serve())

    # ---------------------------------------------------------

    async def shutdown(self):
        """Stop the serial server and release its port."""
        if self._task is None:
            return

        logger.info("Stopping Modbus RTU server | port=%s", self.port)

        if self._server is not None:
            try:
                await self._server.shutdown()
            except Exception:
                logger.exception(
                    "Error during pymodbus serial server shutdown | port=%s",
                    self.port,
                )

        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("Modbus RTU server task ended with error: %s", e)

        self._task = None
        self._server = None
import asyncio
import logging
from typing import Optional

from pymodbus.datastore import ModbusServerContext, ModbusSequentialDataBlock, ModbusDeviceContext
from pymodbus.server import StartAsyncTcpServer, StartAsyncSerialServer

logger = logging.getLogger(__name__)

def create_device_context() -> ModbusDeviceContext:
    di_block = ModbusSequentialDataBlock(0, [0] * 100)
    co_block = ModbusSequentialDataBlock(0, [0] * 100)
    ir_block = ModbusSequentialDataBlock(0, [0] * 40000)
    hr_block = ModbusSequentialDataBlock(0, [0] * 40000)

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

    Uses a TCP socket as the transport layer. Register storage and
    encode/decode logic are provided by the module-level functions
    ``write_telemetry_registers``, ``write_command_registers``, and
    ``collect_write_instructions``.

    Parameters
    ----------
    address : str
        Bind address, e.g. ``"127.0.0.1"`` or ``"0.0.0.0"`` for Docker.
    port : int
        TCP port number.
    unit_id : int
        Modbus slave / unit identifier (1–247).

    Example
    -------
    .. code-block:: python

        import asyncio
        from dertwin.protocol.modbus import ModbusTCPSimulator

        tcp = ModbusTCPSimulator("127.0.0.1", 5020, unit_id=1)

        async def run():
            await tcp.run_server()
            await asyncio.sleep(10)
            await tcp.shutdown()

        asyncio.run(run())
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

    # ---------------------------------------------------------

    async def run_server(self):
        """Start the asynchronous Modbus TCP server."""

        logger.info(
            "Starting Modbus TCP server | %s:%s | unit=%s",
            self.address,
            self.port,
            self.unit_id,
        )

        self._task = asyncio.create_task(
            StartAsyncTcpServer(
                context=self.context,
                address=(self.address, self.port),
            )
        )

    # ---------------------------------------------------------

    async def shutdown(self):
        """Stop the TCP server and cancel background task."""

        if self._task:
            logger.info(
                "Stopping Modbus TCP server | %s:%s",
                self.address,
                self.port,
            )

            self._task.cancel()

            try:
                await self._task
            except (asyncio.CancelledError, Exception) as e:
                if not isinstance(e, asyncio.CancelledError):
                    logger.warning("Modbus TCP server task ended with error: %s", e)

            self._task = None



# ==========================================================
# MODBUS RTU
# ==========================================================

class ModbusRTUSimulator:
    """
    Asynchronous Modbus RTU server for a single simulated device.

    Uses a serial port (physical or virtual) as the transport layer.
    Register storage and encode/decode logic are shared with the TCP
    implementation — reuse ``write_telemetry_registers``,
    ``write_command_registers``, and ``collect_write_instructions``
    from ``dertwin.protocol.modbus`` exactly as you would with
    ``ModbusTCPSimulator``.

    Parameters
    ----------
    port : str
        Serial port path, e.g. ``"/dev/ttyUSB0"`` or a virtual port
        created by ``socat`` for testing.
    unit_id : int
        Modbus slave / unit identifier (1–247).
    baudrate : int
        Serial baud rate. Default ``9600``.
    bytesize : int
        Number of data bits (5–8). Default ``8``.
    parity : str
        Parity setting: ``"N"`` (none), ``"E"`` (even), ``"O"`` (odd).
        Default ``"N"``.
    stopbits : int
        Number of stop bits (1 or 2). Default ``1``.
    timeout : float
        Serial read timeout in seconds. Default ``1.0``.

    Example
    -------
    .. code-block:: python

        import asyncio
        from dertwin.protocol.modbus import (
            ModbusRTUSimulator,
            write_telemetry_registers,
            write_command_registers,
            collect_write_instructions,
        )

        rtu = ModbusRTUSimulator(
            port="/dev/ttyUSB0",
            unit_id=1,
            baudrate=9600,
        )

        async def run():
            await rtu.run_server()
            await asyncio.sleep(30)
            await rtu.shutdown()

        asyncio.run(run())
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
            self._task = asyncio.create_task(
                StartAsyncSerialServer(
                    context=self.context,
                    port=self.port,
                    baudrate=self.baudrate,
                    bytesize=self.bytesize,
                    parity=self.parity,
                    stopbits=self.stopbits,
                    timeout=self.timeout,
                )
            )
        except Exception as e:
            logger.warning(
                "Failed to create Modbus RTU server task | port=%s | error=%s",
                self.port,
                e,
            )

    # ---------------------------------------------------------

    async def shutdown(self):
        """Stop the serial server and cancel background task."""

        if self._task:
            logger.info(
                "Stopping Modbus RTU server | port=%s",
                self.port,
            )

            self._task.cancel()

            try:
                await self._task
            except (asyncio.CancelledError, Exception) as e:
                if not isinstance(e, asyncio.CancelledError):
                    logger.warning("Modbus RTU server task ended with error: %s", e)

            self._task = None

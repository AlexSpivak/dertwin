#!/bin/bash
set -e

CONFIG_PATH="${CONFIG_PATH:-/app/configs/simple_config.json}"
RTU_BRIDGE_BASE_PORT="${RTU_BRIDGE_BASE_PORT:-56001}"

# ---------------------------------------------------------------
# For each RTU port in the config:
#
#   1. Create a PTY pair:
#      /tmp/dertwin_pv  <-->  /tmp/dertwin_pv_bridge
#      (for simulator)        (for TCP bridge)
#
#   2. Relay the bridge PTY to a TCP port using a custom script
#      that keeps both sides open and forwards bytes bidirectionally.
#
# The host then runs:
#   socat pty,raw,echo=0,link=/tmp/dertwin_pv_client tcp:localhost:56001
# ---------------------------------------------------------------

setup_rtu_ports() {
    RTU_PORTS=$(python3 -c "
import json
with open('${CONFIG_PATH}') as f:
    config = json.load(f)
for asset in config.get('assets', []):
    for proto in asset.get('protocols', []):
        if proto.get('kind') == 'modbus_rtu':
            print(proto['port'])
" 2>/dev/null || true)

    if [ -z "$RTU_PORTS" ]; then
        echo "[entrypoint] No RTU ports found — skipping socat setup"
        return
    fi

    BRIDGE_PORT=$RTU_BRIDGE_BASE_PORT

    for SIM_PORT in $RTU_PORTS; do
        BASENAME=$(basename "$SIM_PORT")
        BRIDGE_PTY="/tmp/${BASENAME}_bridge"

        mkdir -p "$(dirname "$SIM_PORT")"

        # Step 1: Create PTY pair — simulator end and bridge end
        echo "[entrypoint] Creating serial pair: $SIM_PORT <-> $BRIDGE_PTY"
        socat \
            pty,raw,echo=0,link="$SIM_PORT" \
            pty,raw,echo=0,link="$BRIDGE_PTY" &
        sleep 0.3

        # Step 2: Bridge the PTY to TCP using a Python relay
        # This keeps the PTY open and relays bytes to/from TCP clients
        echo "[entrypoint] Bridging $BRIDGE_PTY -> TCP port $BRIDGE_PORT"
        python3 -c "
import asyncio, serial, sys

SERIAL_PORT = '$BRIDGE_PTY'
TCP_PORT = $BRIDGE_PORT

class SerialTCPBridge:
    def __init__(self):
        self.ser = None
        self.clients = set()

    def open_serial(self):
        import serial as pyserial
        self.ser = pyserial.Serial(SERIAL_PORT, 9600, timeout=0)

    async def handle_client(self, reader, writer):
        self.clients.add(writer)
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                self.ser.write(data)
        except Exception:
            pass
        finally:
            self.clients.discard(writer)
            writer.close()

    async def serial_to_tcp(self):
        loop = asyncio.get_event_loop()
        while True:
            data = await loop.run_in_executor(None, self.ser.read, 4096)
            if data:
                for w in list(self.clients):
                    try:
                        w.write(data)
                        await w.drain()
                    except Exception:
                        self.clients.discard(w)
            else:
                await asyncio.sleep(0.01)

    async def run(self):
        self.open_serial()
        server = await asyncio.start_server(self.handle_client, '0.0.0.0', TCP_PORT)
        print(f'[bridge] {SERIAL_PORT} <-> TCP :{TCP_PORT}', flush=True)
        await asyncio.gather(
            server.serve_forever(),
            self.serial_to_tcp(),
        )

asyncio.run(SerialTCPBridge().run())
" &
        sleep 0.3

        echo "[entrypoint]   Host: socat pty,raw,echo=0,link=/tmp/${BASENAME}_client tcp:localhost:${BRIDGE_PORT} &"

        BRIDGE_PORT=$((BRIDGE_PORT + 1))
    done

    sleep 0.5
    echo "[entrypoint] RTU bridges ready"
}

# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------

echo "[entrypoint] Config: $CONFIG_PATH"

setup_rtu_ports

echo "[entrypoint] Starting DERTwin simulator"
exec python -m dertwin.main -c "$CONFIG_PATH"
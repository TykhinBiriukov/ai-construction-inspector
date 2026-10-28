"""Run Inspector and a temporary HTTPS tunnel on this Mac without printing secrets."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
RUNTIME = ROOT / '.local' / 'mac-host'
STATE = RUNTIME / 'state.json'


def save_state(value):
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(STATE)


def read_state():
    try:
        return json.loads(STATE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def process_identity(pid):
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart=', '-o', 'args='],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ''


def running(state):
    pid = state.get('pid')
    identity = process_identity(pid) if isinstance(pid, int) and pid > 1 else ''
    return bool(identity and identity == state.get('identity') and str(SCRIPT) in identity)


class AdoptedProcess:
    """Track an existing, identity-checked tunnel after a supervisor upgrade."""
    def __init__(self, record):
        self.pid = record['pid']
        self.identity = record['identity']
        if not self.identity or process_identity(self.pid) != self.identity:
            raise RuntimeError('Owned process identity changed; refusing to adopt it.')

    def poll(self):
        return None if process_identity(self.pid) == self.identity else 0

    def terminate(self):
        if self.poll() is None:
            os.kill(self.pid, signal.SIGTERM)

    def kill(self):
        if self.poll() is None:
            os.kill(self.pid, signal.SIGKILL)

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while self.poll() is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired('owned process', timeout)
            time.sleep(0.1)
        return 0


def upgrade_record(state):
    """Version 1 cannot restart its backend without also stopping the tunnel."""
    output = subprocess.check_output(['ps', '-axo', 'pid=', '-o', 'ppid=', '-o', 'args='], text=True)
    children = []
    for line in output.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) == 3 and int(fields[1]) == state['pid']:
            children.append((int(fields[0]), fields[2]))
    tunnels = [pid for pid, args in children if str(ROOT / 'tools' / 'cloudflared') in args
               and f'http://127.0.0.1:{state["port"]}' in args]
    servers = [pid for pid, args in children if '-m uvicorn ai_inspector.main:app' in args
               and f'--port {state["port"]}' in args]
    if len(tunnels) != 1 or len(servers) != 1 or not running(state):
        raise RuntimeError('Could not identify the exact owned tunnel and backend; no process changed.')
    url = state.get('public_url', '')
    if not re.fullmatch(r'https://[a-z0-9-]+\.trycloudflare\.com', url):
        raise RuntimeError('Missing verified current tunnel URL.')
    return {'url': url, 'port': state['port'],
            'tunnel': {'pid': tunnels[0], 'identity': process_identity(tunnels[0])},
            'server': {'pid': servers[0], 'identity': process_identity(servers[0])}}


def prepare():
    os.umask(0o077)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    env = ROOT / '.env'
    if not env.exists():
        # Keep this deployment separate from browser-test fixtures.
        with env.open('x') as handle:
            handle.write('INSPECTOR_DATA_DIR=.local/mac-host-data\n')
    env.chmod(0o600)
    subprocess.run([sys.executable, str(ROOT / 'scripts/prepare_remote_config.py'), '--ensure-tokens'],
                   cwd=ROOT, check=True)


def run(port, adoption=None):
    os.umask(0o077)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    lock = (RUNTIME / 'supervisor.lock').open('a')
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('An Inspector Mac host supervisor is already running.')
    state = {'pid': os.getpid(), 'identity': process_identity(os.getpid()), 'status': 'starting',
             'port': port, 'supervisor_version': 2, 'server_generation': 0}
    save_state(state)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    reload_backend = threading.Event()
    signal.signal(signal.SIGHUP, lambda *_: reload_backend.set())
    children, logs = [], []

    def spawn(name, command, env=None):
        log = (RUNTIME / f'{name}.log').open('ab')
        logs.append(log)
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT)
        children.append(child)
        return child

    try:
        binary = ROOT / 'tools' / 'cloudflared'
        if not binary.is_file():
            raise RuntimeError('Install the official macOS cloudflared binary at tools/cloudflared.')
        prepare()
        if adoption:
            if adoption['port'] != port:
                raise RuntimeError('Adoption port mismatch.')
            tunnel = AdoptedProcess(adoption['tunnel'])
            children.append(tunnel)
            previous_server = AdoptedProcess(adoption['server'])
            previous_server.terminate()
            previous_server.wait(timeout=20)
            url = adoption['url']
        else:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', port))
            # Avoid accidentally using an unrelated named-tunnel configuration.
            config = RUNTIME / 'cloudflared.yaml'
            config.write_text('{}\n')
            (RUNTIME / 'tunnel.log').write_text('')
            tunnel = spawn('tunnel', [str(binary), 'tunnel', '--config', str(config), '--url',
                                     f'http://127.0.0.1:{port}', '--protocol', 'http2', '--no-autoupdate'])
            url = None
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline and not stop.is_set():
                if tunnel.poll() is not None:
                    raise RuntimeError('Tunnel exited; inspect .local/mac-host/tunnel.log.')
                match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', (RUNTIME / 'tunnel.log').read_text(errors='replace'))
                if match:
                    url = match.group(0)
                    break
                stop.wait(0.25)
        if not url:
            raise RuntimeError('No HTTPS address received from Cloudflare within 45 seconds.')
        env = dict(os.environ, INSPECTOR_PUBLIC_BASE_URL=url)
        def start_server():
            child = spawn('server', [sys.executable, '-m', 'uvicorn', 'ai_inspector.main:app',
                                    '--host', '127.0.0.1', '--port', str(port), '--workers', '1',
                                    '--ws-max-size', '65536'], env)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not stop.is_set():
                if child.poll() is not None:
                    raise RuntimeError('Inspector exited; inspect .local/mac-host/server.log.')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as response:
                        if json.load(response).get('status') == 'ok':
                            return child
                except (OSError, ValueError):
                    pass
                stop.wait(0.25)
            raise RuntimeError('Inspector did not become healthy.')

        server = start_server()
        subprocess.run([sys.executable, str(ROOT / 'scripts/prepare_remote_config.py'), '--public-url', url],
                       cwd=ROOT, check=True)
        for filename in ('web-connection.json', 'ios-connection.json'):
            (ROOT / '.local' / filename).chmod(0o600)
        # Prevent idle sleep while hosting; manual sleep/lid closure can still interrupt it.
        spawn('awake', ['/usr/bin/caffeinate', '-i', '-w', str(os.getpid())])
        state.update(status='running', public_url=url, server_generation=1)
        save_state(state)
        while not stop.wait(1):
            if reload_backend.is_set():
                reload_backend.clear()
                # Validate before stopping a healthy backend. Secrets stay out of logs.
                from ai_inspector.config import Settings
                Settings.from_env(ROOT / '.env')
                server.terminate()
                server.wait(timeout=20)
                server = start_server()
                state['server_generation'] += 1
                save_state(state)
            if server.poll() is not None or tunnel.poll() is not None:
                raise RuntimeError('Inspector or tunnel exited; use status and inspect its log before restarting.')
    except Exception as error:
        state.update(status='failed', error=str(error))
        save_state(state)
        print(str(error), file=sys.stderr, flush=True)
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in reversed(children):
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=8)
        for log in logs:
            log.close()
        if state['status'] != 'failed':
            state['status'] = 'stopped'
        save_state(state)
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'stop', 'status', 'restart', 'run'))
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--adopt', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Use a port between 1024 and 65535.')
    if sys.platform != 'darwin':
        parser.error('This host helper is for macOS.')
    if args.action == 'run':
        run(args.port, json.loads(args.adopt.read_text()) if args.adopt else None)
        return
    state = read_state()
    alive = running(state)
    if args.action == 'status':
        print(json.dumps({'running': alive, **{k: state[k] for k in ('status', 'public_url', 'port', 'error') if k in state}}, indent=2))
    elif args.action == 'stop':
        if not alive:
            print('No matching Inspector host process is running.')
            return
        os.kill(state['pid'], signal.SIGTERM)
        deadline = time.monotonic() + 30
        while running(state) and time.monotonic() < deadline:
            time.sleep(0.25)
        if running(state):
            raise SystemExit('Shutdown is still in progress. Inspect status; no unrelated process was signalled.')
        print('Inspector, HTTPS tunnel and idle-sleep assertion stopped. Data preserved.')
    else:
        adoption_path = None
        if args.action == 'restart':
            if not alive:
                raise SystemExit('No matching running host. Use start.')
            from ai_inspector.config import Settings
            Settings.from_env(ROOT / '.env')
            if state.get('supervisor_version', 1) >= 2:
                generation = state.get('server_generation', 0)
                os.kill(state['pid'], signal.SIGHUP)
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline and running(state):
                    current = read_state()
                    if current.get('server_generation', 0) > generation:
                        print('Backend restarted; HTTPS URL preserved:', current['public_url'])
                        return
                    time.sleep(0.25)
                raise SystemExit('Restart did not confirm readiness; inspect status and logs.')
            # One-time upgrade of our v1 supervisor: keep its tunnel process alive.
            # The old backend is stopped gracefully by the new supervisor.
            record = upgrade_record(state)
            os.umask(0o077)
            adoption_path = RUNTIME / 'adoption.json'
            adoption_path.write_text(json.dumps(record, indent=2) + '\n')
            if not running(state):
                raise SystemExit('Supervisor identity changed; no process signalled.')
            os.kill(state['pid'], signal.SIGKILL)
            deadline = time.monotonic() + 5
            while running(state) and time.monotonic() < deadline:
                time.sleep(0.1)
            if running(state):
                raise SystemExit('Old supervisor did not exit; tunnel and backend were not changed.')
            alive = False
            args.port = state['port']
        if alive:
            print('Inspector host already running:', state.get('public_url', 'starting'))
            return
        os.umask(0o077)
        RUNTIME.mkdir(parents=True, exist_ok=True)
        with (RUNTIME / 'supervisor.log').open('ab') as log:
            command = [sys.executable, str(SCRIPT), 'run', '--port', str(args.port)]
            if adoption_path:
                command += ['--adopt', str(adoption_path)]
            process = subprocess.Popen(command,
                                       cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 70
        while time.monotonic() < deadline:
            current = read_state()
            if current.get('pid') == process.pid and current.get('status') == 'running':
                print('Inspector HTTPS:', current['public_url'])
                print('Private operator credentials: .local/web-connection.json')
                print('Stop: .venv/bin/python scripts/host_mac.py stop')
                return
            if process.poll() is not None:
                raise SystemExit('Startup failed. Inspect .local/mac-host/supervisor.log.')
            time.sleep(0.25)
        raise SystemExit('Startup is still pending. Check scripts/host_mac.py status before retrying.')


if __name__ == '__main__':
    main()

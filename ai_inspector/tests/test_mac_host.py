"""Safety checks for retaining only our own tunnel during a backend restart."""
import importlib.util
from pathlib import Path
import unittest
import sys
from unittest.mock import patch

host = None
if sys.platform != 'win32':
    spec = importlib.util.spec_from_file_location('host_mac', Path(__file__).resolve().parents[1] / 'scripts' / 'host_mac.py')
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)

@unittest.skipIf(host is None, 'Mac host uses POSIX process APIs')

class HostRestartTests(unittest.TestCase):
    def test_reused_pid_is_never_signalled(self):
        with patch.object(host, 'process_identity', return_value='owned start + command'):
            process = host.AdoptedProcess({'pid': 123, 'identity': 'owned start + command'})
        with patch.object(host, 'process_identity', return_value='different process'), patch.object(host.os, 'kill') as kill:
            process.terminate()
            process.kill()
            self.assertEqual(process.poll(), 0)
            kill.assert_not_called()

    def test_upgrade_only_accepts_exact_parent_and_port(self):
        state = {'pid': 100, 'port': 8765, 'public_url': 'https://test.trycloudflare.com'}
        listing = (f'101 100 {host.ROOT}/tools/cloudflared tunnel --url http://127.0.0.1:8765\n'
                   '102 999 python -m uvicorn ai_inspector.main:app --port 8765\n')
        with patch.object(host.subprocess, 'check_output', return_value=listing), patch.object(host, 'running', return_value=True):
            with self.assertRaises(RuntimeError):
                host.upgrade_record(state)

    def test_upgrade_refuses_ambiguous_backend(self):
        state = {'pid': 100, 'port': 8765, 'public_url': 'https://test.trycloudflare.com'}
        listing = (f'101 100 {host.ROOT}/tools/cloudflared tunnel --url http://127.0.0.1:8765\n'
                   '102 100 python -m uvicorn ai_inspector.main:app --port 8765\n'
                   '103 100 python -m uvicorn ai_inspector.main:app --port 8765\n')
        with patch.object(host.subprocess, 'check_output', return_value=listing), patch.object(host, 'running', return_value=True):
            with self.assertRaises(RuntimeError):
                host.upgrade_record(state)

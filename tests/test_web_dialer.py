from pathlib import Path
import shutil
import subprocess
import pytest


def test_browser_dialer_state_machine():
    node=shutil.which('node')
    if node is None: pytest.skip('Node.js is optional; install it to run the browser-controller unit tests.')
    result=subprocess.run([node,'--test',str(Path(__file__).with_name('web_dialer.cjs'))],
        capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr

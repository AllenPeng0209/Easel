"""The public Xiangya command and existing Easel entry must remain usable."""
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('module', ['xiangya', 'easel'])
def test_module_help_uses_the_new_brand_and_keeps_existing_commands(module):
    result = subprocess.run([sys.executable, '-m', module, '--help'], cwd=ROOT,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'usage: xiangya ' in result.stdout
    assert '响芽' in result.stdout and 'Xiangya' in result.stdout
    assert 'Easel' not in result.stdout
    for command in ('chat', 'doctor', 'gateway', 'ping', 'skill', 'web'):
        assert command in result.stdout


def test_new_cli_reports_invalid_commands_without_starting_services():
    result = subprocess.run([sys.executable, '-m', 'xiangya', 'invalid-command'],
                            cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 2
    assert 'xiangya' in result.stderr and 'invalid choice' in result.stderr

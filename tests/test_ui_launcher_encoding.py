"""Current fix: WinPS launches the configured workspace from UTF-8 source/config."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == "nt", "Windows PowerShell launcher behavior")
class WindowsUILauncherEncoding(unittest.TestCase):
    def test_windows_powershell_dispatches_exact_chinese_registry_and_options(self):
        # Intercept only OS process creation; the real Windows PowerShell reads
        # and executes the actual script bytes and original JSON config itself.
        with tempfile.TemporaryDirectory(prefix='rail-launch-中文 ') as temporary:
            workspace=Path(temporary)
            activation=workspace/'.context-rail/active-native-client.json'
            activation.parent.mkdir()
            registry=str(workspace/'接线 目录/registry.json')
            activation.write_text(json.dumps({'layout':{'registry':registry}},ensure_ascii=False),encoding='utf-8')
            launcher=workspace/'Open-ContextRailUI.ps1'
            shutil.copyfile(Path(__file__).resolve().parents[1]/launcher.name,launcher)
            script="""
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$ErrorActionPreference = 'Stop'
$global:railLaunchRecords = New-Object System.Collections.Generic.List[object]
function Start-Process {
    param($FilePath, [string[]]$ArgumentList, $WorkingDirectory, $WindowStyle)
    $global:railLaunchRecords.Add(@{executable=$FilePath;arguments=$ArgumentList;cwd=$WorkingDirectory;window=$WindowStyle})
}
$failure = $null
try { & 'LAUNCHER_PATH' -Port 39876 -IdleTimeout 120 } catch { $failure = $_.ToString() }
@{launches=@($global:railLaunchRecords.ToArray());failure=$failure} | ConvertTo-Json -Depth 8 -Compress
""".replace('LAUNCHER_PATH',str(launcher).replace("'","''"))
            encoded=base64.b64encode(script.encode('utf-16-le')).decode('ascii')
            powershell=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
            result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-EncodedCommand',encoded],capture_output=True,timeout=25)
            observed=json.loads(result.stdout.decode('utf-8-sig'))
            self.assertEqual(len(observed['launches']),1,observed)
            launch=observed['launches'][0]
            self.assertEqual(Path(launch['executable']),workspace/'.venv/Scripts/python.exe')
            self.assertEqual(launch['arguments'],['-m','contextrail.ui_server','--registry','"'+registry+'"','--port','39876','--idle-timeout','120','--open'])
            self.assertEqual(launch['cwd'],str(workspace))
            self.assertEqual(launch['window'],'Hidden')
            self.assertIsNone(observed['failure'])

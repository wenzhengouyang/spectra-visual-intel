"""Install the standalone local account service; never change daily jobs."""
import os
import plistlib
import subprocess
from pathlib import Path

base=Path.home()/'Library/Application Support/SPECTRA'
runtime=base/'runtime'
label='com.spectra.visual-intel.accounts'
plist=Path.home()/'Library/LaunchAgents'/f'{label}.plist'
if __name__=='__main__':
    if not (runtime/'spectra_agent/account_server.py').is_file():
        raise SystemExit('Deploy account files to runtime first')
    logs=base/'data/logs';logs.mkdir(parents=True,exist_ok=True)
    payload={'Label':label,'ProgramArguments':[str(runtime/'.venv-collector/bin/python'),'-m','spectra_agent.account_server','--port','8012','--allow-registration'],
             'WorkingDirectory':str(runtime),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,
             'EnvironmentVariables':{'SPECTRA_ACCOUNT_ORIGIN':'http://127.0.0.1:8012','SPECTRA_ACCOUNT_DB':str(base/'data/accounts.sqlite3')},
             'StandardOutPath':str(logs/'accounts.out.log'),'StandardErrorPath':str(logs/'accounts.err.log')}
    plist.parent.mkdir(parents=True,exist_ok=True)
    if plist.exists():
        from datetime import datetime
        backup=base/'data/deployment-backups'/datetime.now().strftime('account-service-%Y%m%d-%H%M%S')
        backup.mkdir(parents=True);(backup/plist.name).write_bytes(plist.read_bytes())
    plist.write_bytes(plistlib.dumps(payload))
    domain=f'gui/{os.getuid()}'
    subprocess.run(['launchctl','bootout',domain+'/'+label],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(['launchctl','bootstrap',domain,str(plist)],check=True)
    print('Installed local account workbench: http://127.0.0.1:8012/')

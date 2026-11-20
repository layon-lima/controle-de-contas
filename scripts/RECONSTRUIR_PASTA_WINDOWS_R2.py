#!/usr/bin/env python3
"""Restauracao avulsa, de SOMENTE LEITURA do R2/SQLite para uma pasta Windows via SMB.

Nao altera o CONTAS 4, nao chama endpoints de escrita, nao persiste PDFs na VPS.
Roda na VPS; a pasta Windows deve estar acessivel via compartilhamento privado SMB
(preferencialmente por VPN). Todos os PDFs seguem o padrao BOLETOS YYYY/MM - MES.
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import uuid

MESES = ('JANEIRO','FEVEREIRO','MARÇO','ABRIL','MAIO','JUNHO',
         'JULHO','AGOSTO','SETEMBRO','OUTUBRO','NOVEMBRO','DEZEMBRO')
MAX_PDF = 30 * 1024 * 1024
DEFAULT_CONTAINER = 'contas-web-original-contas-web-1'

# Este trecho roda SOMENTE dentro do container original, com credenciais R2
# ja instaladas nele. O stdout e binario; nenhuma chave ou senha e exibida.
SOURCE = r'''
import os,sys,sqlite3,datetime,json,hashlib,struct,re
from pathlib import Path
import boto3
from botocore.config import Config
cfg_path=Path(os.environ.get('CONTAS_R2_CONFIG','/data/ControleContas/r2-agent.json'))
try:
 cfg=json.loads(cfg_path.read_text(encoding='utf-8'))
 app=Path(os.environ.get('CONTAS_APP_DIR','/data/ControleContas'))
 db=app/'data'/'controle_contas.db'
 if not db.is_file():
  raise RuntimeError('SQLite das contas nao encontrado: '+str(db))
 conn=sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)
 conn.row_factory=sqlite3.Row
 rows={int(r['id']):dict(r) for r in conn.execute('SELECT id,vencimento,observacoes FROM contas')}
 client=boto3.client('s3',endpoint_url='https://'+cfg['account_id']+'.r2.cloudflarestorage.com',
  aws_access_key_id=cfg['access_key_id'],aws_secret_access_key=cfg['secret_access_key'],
  region_name='auto',config=Config(signature_version='s3v4',connect_timeout=8,
  read_timeout=25,retries={'max_attempts':2}))
 bucket=cfg.get('bucket','controle-contas-pdfs')
except Exception as exc:
 print('ERRO_FONTE_R2: '+str(exc),file=sys.stderr,flush=True)
 sys.exit(3)
def send(h,data=b''):
 raw=json.dumps(h,ensure_ascii=False,separators=(',',':')).encode('utf-8')
 sys.stdout.buffer.write(struct.pack('>I',len(raw)))
 sys.stdout.buffer.write(raw)
 if data:sys.stdout.buffer.write(data)
 sys.stdout.buffer.flush()
count=0;orphans=0;errors=0;ignored=0
try:
 paginator=client.get_paginator('list_objects_v2')
 for page in paginator.paginate(Bucket=bucket,Prefix='accounts/'):
  for obj in page.get('Contents',[]):
   key=obj['Key']
   match=re.fullmatch(r'accounts/([0-9]+)/original\.pdf',key)
   if not match:
    ignored+=1
    continue
   aid=int(match.group(1))
   try:
    result=client.get_object(Bucket=bucket,Key=key)
    size=int(result.get('ContentLength',0))
    if size<5 or size>30*1024*1024:
     raise ValueError('PDF fora dos limites de 5 bytes a 30 MB')
    content=result['Body'].read(size+1)
    if len(content)!=size or not content.startswith(b'%PDF-'):
     raise ValueError('PDF incompleto ou assinatura invalida')
    digest=hashlib.sha256(content).hexdigest()
    claimed=(result.get('Metadata',{}) or {}).get('sha256','').lower()
    if claimed and claimed!=digest:
     raise ValueError('SHA256 do R2 nao confere')
    account=rows.get(aid)
    if account is None:orphans+=1
    send({'id':aid,'key':key,'size':size,'sha256':digest,
      'vencimento':account['vencimento'] if account else None,
      'observacoes':account['observacoes'] if account else None,
      'sem_cadastro':account is None},content)
    count+=1
   except Exception as exc:
    errors+=1
    send({'id':aid,'key':key,'error':str(exc)})
 send({'done':True,'sources':count,'errors':errors,'orphans':orphans,'other_keys':ignored})
except Exception as exc:
 print('ERRO_LISTAGEM_R2: '+str(exc),file=sys.stderr,flush=True)
 sys.exit(4)
'''


def fail(msg: str):
    raise RuntimeError(msg)


def prompt(msg: str, default: str = '') -> str:
    hint = f' [{default}]' if default else ''
    value = input(msg + hint + ': ').strip()
    return value or default


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''):
            h.update(part)
    return h.hexdigest()


def safe_name(value) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', str(value or 'BOLETO'))
    name = ' '.join(name.split()).strip()
    if name.casefold().endswith('.pdf'):
        name = name[:-4]
    name = name[:110].rstrip('. ') or 'BOLETO'
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM\d|LPT\d)', name, re.I):
        name='BOLETO '+name
    return name


def relative_destination(header: dict) -> Path:
    aid=int(header['id'])
    if header['sem_cadastro']:
        return Path('_R2_SEM_CADASTRO') / f'{aid} - original.pdf'
    due=str(header.get('vencimento') or '')[:10]
    try:
        date=dt.date.fromisoformat(due)
    except (ValueError,TypeError):
        return Path('_R2_SEM_VENCIMENTO') / f'{aid} - {safe_name(header.get("observacoes"))}.pdf'
    month=f'{date.month:02d} - {MESES[date.month-1]}'
    return Path(f'BOLETOS {date.year}') / month / f'{aid} - {safe_name(header.get("observacoes"))}.pdf'


def valid_subdir(value: str) -> Path:
    if not value or value in ('.','/'):
        return Path('.')
    p=Path(value.replace('\\','/'))
    if p.is_absolute() or any(x in ('..','') for x in p.parts):
        fail('Subpasta do Windows invalida; use um caminho RELATIVO sem ..')
    return p


def smb_path(value: str):
    normalized=value.strip().replace('\\','/')
    match=re.fullmatch(r'//([^/]+)/([^/]+)',normalized)
    if not match:
        fail('Compartilhamento invalido. Exemplo: //100.101.102.103/FNH_DOCUMENTOS')
    return normalized,match.group(1)


def private_smb_available(host: str):
    # Nao recomendamos nem tentamos publicar SMB na Internet.
    addresses=socket.getaddrinfo(host,445,type=socket.SOCK_STREAM)
    tailscale_ipv4=ipaddress.ip_network('100.64.0.0/10')
    for addr in addresses:
        ip=ipaddress.ip_address(addr[4][0])
        if not (ip.is_private or ip.is_loopback or ip.is_link_local or ip in tailscale_ipv4):
            fail('O IP de destino e publico. Nao exponha SMB/445; conecte Windows e VPS por VPN privada primeiro.')
    last=None
    for addr in addresses:
        try:
            sock=socket.socket(addr[0],socket.SOCK_STREAM)
            sock.settimeout(4)
            sock.connect(addr[4]);sock.close()
            return
        except OSError as exc:
            last=exc
    fail('A VPS nao alcanca o Windows na porta SMB/445 ('+str(last)+'). '
         'Use VPN privada entre VPS e Windows ou um compartilhamento ja montado na VPS.')


def find_existing_mount() -> Path | None:
    out=subprocess.run(['findmnt','-rn','-t','cifs','-o','TARGET,SOURCE'],
                       text=True,capture_output=True,check=False)
    if out.returncode:
        return None
    for line in out.stdout.splitlines():
        parts=line.split(None,1)
        if len(parts)==2 and 'FNH_DOCUMENTOS' in parts[1].upper():
            return Path(parts[0])
    return None


def mount_share(share: str, cred_file: str|None) -> Path:
    if os.geteuid()!=0:
        fail('Para montar um compartilhamento SMB, execute como root.')
    if not shutil.which('mount.cifs'):
        fail('Ferramenta SMB ausente. Instale uma vez: apt-get update && apt-get install -y cifs-utils')
    share,host=smb_path(share)
    private_smb_available(host)
    user,temporary=None,None
    if cred_file:
        creds=Path(cred_file)
        if not creds.is_file() or creds.stat().st_mode & 0o077:
            fail('Credenciais SMB precisam estar em arquivo root-only (chmod 600).')
    else:
        username=prompt('Usuario do compartilhamento Windows',os.environ.get('CONTAS_SMB_USER','fnh_pdf'))
        password=getpass.getpass('Senha do usuario Windows (nao aparece): ')
        domain=os.environ.get('CONTAS_SMB_DOMAIN','')
        if not username or not password:
            fail('Usuario e senha SMB sao obrigatorios.')
        fd,filename=tempfile.mkstemp(prefix='.contas-smb-',dir='/root')
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            f.write('username='+username+'\npassword='+password+'\n')
            if domain:f.write('domain='+domain+'\n')
        creds=Path(filename);temporary=creds
    mountpoint=Path(tempfile.mkdtemp(prefix='contas-win-',dir='/mnt'))
    try:
        cmd=['mount','-t','cifs',share,str(mountpoint),'-o',
             f'credentials={creds},vers=3.0,iocharset=utf8,noserverino,rw']
        p=subprocess.run(cmd,text=True,capture_output=True,timeout=45)
        if p.returncode or not os.path.ismount(mountpoint):
            fail('Nao foi possivel montar a pasta Windows. '+p.stderr.strip()[-400:])
        print('Compartilhamento Windows montado temporariamente.',flush=True)
        return mountpoint
    except Exception:
        mountpoint.rmdir()
        raise
    finally:
        if temporary:temporary.unlink(missing_ok=True)


def read_exact(stream,size):
    result=bytearray()
    while len(result)<size:
        part=stream.read(min(size-len(result),1024*1024))
        if not part:fail('A transmissão R2→VPS foi interrompida antes de completar o arquivo.')
        result.extend(part)
    return bytes(result)


def read_frame(stream):
    length=stream.read(4)
    if not length:return None
    if len(length)<4:fail('Cabeçalho incompleto vindo do R2.')
    n=struct.unpack('>I',length)[0]
    if not (2<=n<=32*1024):fail('Cabeçalho fora do limite seguro.')
    return json.loads(read_exact(stream,n))


def choose_target(folder:Path, intended:Path, aid:int)->Path:
    """O app Windows reutiliza o nome de um PDF existente do MESMO ID."""
    if intended.exists():return intended
    current=sorted(p for p in folder.glob(f'{aid} - *.pdf') if p.is_file())
    return current[0] if current else intended


def restore_one(root:Path,header:dict,content:bytes,apply:bool,backups:Path,log):
    rel=relative_destination(header)
    target=root/rel
    expected=header['sha256']
    if len(content)!=header['size'] or hashlib.sha256(content).hexdigest()!=expected:
        fail('Integridade divergente no PDF da conta '+str(header['id']))
    if not content.startswith(b'%PDF-'):
        fail('Arquivo nao e PDF: conta '+str(header['id']))
    if not apply:
        log({'id':header['id'],'state':'PLANEJADO','path':str(rel)})
        return 'planned'
    folder=target.parent;folder.mkdir(parents=True,exist_ok=True)
    target=choose_target(folder,target,int(header['id']))
    # Se o arquivo atual ja e identico, nao fazer NENHUMA escrita.
    if target.is_file() and sha256(target)==expected:
        log({'id':header['id'],'state':'IGUAL','path':str(target.relative_to(root))})
        return 'equal'
    temp=folder/('.r2-recuperacao-'+str(header['id'])+'-'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('xb') as output:
            output.write(content);output.flush();os.fsync(output.fileno())
        if sha256(temp)!=expected:
            fail('Erro de verificacao SHA256 no destino Windows: '+str(rel))
        if target.exists():
            # NUNCA apagar arquivo local diferente: preserva backup antes
            # de substituir pela copia atual do R2 (que tem precedencia).
            backup=backups/target.relative_to(root)
            backup.parent.mkdir(parents=True,exist_ok=True)
            if backup.exists():
                backup=backup.with_name(backup.stem+'-'+uuid.uuid4().hex[:8]+backup.suffix)
            shutil.copy2(target,backup)
            if sha256(backup)!=sha256(target):
                fail('Backup Windows nao passou na verificacao; nao substitui a conta '+str(header['id']))
            os.replace(temp,target)
            state='ATUALIZADO_BACKUP'
        else:
            os.replace(temp,target)
            state='COPIADO'
        if sha256(target)!=expected:
            fail('Copia no Windows nao conferiu apos gravacao: '+str(rel))
        log({'id':header['id'],'state':state,'path':str(target.relative_to(root))})
        return state
    finally:
        temp.unlink(missing_ok=True)


def docker_container(name):
    if not shutil.which('docker'):
        fail('Docker nao encontrado nesta VPS.')
    p=subprocess.run(['docker','inspect','--format','{{.State.Running}}',name],
        capture_output=True,text=True)
    if p.returncode==0 and p.stdout.strip()=='true':return name
    items=subprocess.run(['docker','ps','--format','{{.Names}}'],capture_output=True,text=True)
    matches=[x for x in items.stdout.splitlines() if 'contas-web' in x.lower()]
    if len(matches)==1:return matches[0]
    fail('Container CONTAS nao foi encontrado. Informe com --container. Encontrados: '+str(matches))


def restore(args):
    container=docker_container(args.container)
    mounted=False
    if args.mounted:
        mountpoint=Path(args.mounted).resolve()
        if not mountpoint.is_dir() or not os.path.ismount(mountpoint):
            fail('Informe um ponto de montagem Windows existente (CIFS) em --mounted.')
        fstype=subprocess.run(['findmnt','-n','-o','FSTYPE','--target',str(mountpoint)],
            capture_output=True,text=True,check=False)
        if fstype.stdout.strip().lower() not in ('cifs','smb3'):
            fail('O destino --mounted nao e um compartilhamento SMB/CIFS. '                 'Recuso gravar PDFs no disco da VPS.')
    else:
        mountpoint=find_existing_mount()
        if mountpoint is None:
            share=args.share or os.environ.get('CONTAS_WINDOWS_SHARE')
            if not share:
                print('A pasta escolhida no navegador NAO e visivel na VPS por si so.')
                share=prompt('Compartilhamento Windows acessivel por VPN/SMB (//IP/FNH_DOCUMENTOS)')
            mountpoint=mount_share(share,args.credentials)
            mounted=True
        else:print('Usando montagem SMB existente:',mountpoint)
    try:
        root=(mountpoint/valid_subdir(args.subdir))
        if not str(root.resolve()).startswith(str(mountpoint.resolve())+os.sep) and root.resolve()!=mountpoint.resolve():
            fail('Subpasta fora do compartilhamento Windows.')
        if args.apply:
            root.mkdir(parents=True,exist_ok=True)
            # Falha cedo se for somente leitura, ANTES de baixar PDFs do R2.
            with tempfile.NamedTemporaryFile(prefix='.teste-restauracao-',dir=root,delete=True) as f:
                f.write(b'teste');f.flush()
        else:
            print('SIMULACAO: sem gravar PDFs. Use --apply para copiar tudo.')
        stamp=dt.datetime.now().strftime('%Y%m%d_%H%M%S')
        path=Path('/root')/('RELATORIO_RESTAURACAO_CONTAS_R2_'+stamp+'.jsonl')
        counts={'COPIADO':0,'IGUAL':0,'ATUALIZADO_BACKUP':0,'PLANEJADO':0,'ERRO':0}
        backup=root/'_BACKUP_ANTES_RESTAURACAO_R2'/stamp
        print('Destino:',root)
        print('Origem: bucket R2 do proprio CONTAS (somente leitura); banco SQLite (somente leitura).')
        print('Operacao:', 'COPIAR PARA WINDOWS' if args.apply else 'SIMULAR')
        proc=subprocess.Popen(['docker','exec','-i',container,'python3','-u','-c',SOURCE],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        done=None
        try:
            with path.open('w',encoding='utf-8') as report:
                os.chmod(path,0o600)
                def log(entry):
                    report.write(json.dumps(entry,ensure_ascii=False)+'\n');report.flush()
                while True:
                    header=read_frame(proc.stdout)
                    if header is None:break
                    if header.get('done'):
                        done=header;break
                    aid=int(header.get('id',0))
                    if 'error' in header:
                        counts['ERRO']+=1
                        print(f'[{aid}] ERRO R2: {header["error"]}',flush=True)
                        log({'id':aid,'state':'ERRO_R2','message':header['error']})
                        continue
                    data=read_exact(proc.stdout,int(header['size']))
                    try:
                        state=restore_one(root,header,data,args.apply,backup,log)
                        counts[state.upper()]+=1
                        print(f'[{aid}] {state.upper()} -> {relative_destination(header)}',flush=True)
                    except Exception as exc:
                        counts['ERRO']+=1
                        print(f'[{aid}] ERRO WINDOWS: {exc}',flush=True)
                        log({'id':aid,'state':'ERRO_WINDOWS','message':str(exc)})
            proc.wait(timeout=30)
            stderr=proc.stderr.read().decode('utf-8','replace')[-2500:]
            if proc.returncode!=0 or done is None:
                fail('Nao foi possivel completar a leitura do R2. '+stderr)
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
        print('\n=== RESULTADO ===')
        for key,val in counts.items():print(f'{key}: {val}')
        print('Objetos R2:',done.get('sources',0),'| Sem cadastro:',done.get('orphans',0),
              '| Chaves fora do formato:',done.get('other_keys',0))
        print('Relatorio (somente root):',path)
        if counts['ERRO'] or done.get('errors'):
            print('ATENCAO: ha erros; confira o relatorio, nao execute repetidamente sem verificar.')
            return 2
        return 0

    finally:
        if mounted:
            result=subprocess.run(['umount',str(mountpoint)],capture_output=True,text=True)
            if result.returncode:
                print('ATENCAO: montagem Windows nao foi desmontada: '+str(mountpoint),file=sys.stderr)
            else:
                try:mountpoint.rmdir()
                except OSError:pass


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true',help='Copiar para o Windows. Sem isso simula.')
    parser.add_argument('--share',help='Compartilhamento privado: //IP-PRIVADO/FNH_DOCUMENTOS')
    parser.add_argument('--mounted',help='Ponto de montagem CIFS existente no Ubuntu')
    parser.add_argument('--subdir',default=os.environ.get('CONTAS_WINDOWS_SUBDIR','BOLETOS'),
        help='Subpasta do compartilhamento que corresponde a Pasta boletos (padrao BOLETOS)')
    parser.add_argument('--credentials',help='Arquivo usuario/senha root-only (chmod 600)')
    parser.add_argument('--container',default=DEFAULT_CONTAINER)
    args=parser.parse_args()
    try:return restore(args)
    except (OSError,RuntimeError,ValueError,subprocess.TimeoutExpired) as exc:
        print('\nERRO: '+str(exc),file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
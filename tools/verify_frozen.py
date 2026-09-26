"""별도 자료 폴더와 합성 파이프로 exe의 시작·네이티브 계산·정상 종료를 확인한다. 게임에는 연결하지 않는다."""
import ctypes, json, os, subprocess, sys, threading, time, uuid
from pathlib import Path
from ctypes import wintypes as wt
root=Path(__file__).resolve().parents[1]
exe=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else root/'dist/BallxPitCompanion/BallxPitCompanion.exe'
name='bxp-smoke-'+uuid.uuid4().hex
profile=root/'build'/name
profile.mkdir()
(profile/'settings.json').write_text(json.dumps({'auto_install_mod':False,'watch_enabled':False,'start_with_windows':False,'hud_auto_show':False}),encoding='utf-8')
env=dict(os.environ)
env.pop('BXP_NO_NATIVE',None)
for key in ('USERNAME','LOGNAME','USER','LNAME'):env[key]=name
env['BXP_APP_DIR']=str(profile);env['BXP_CATALOG_FILE']=str(profile/'catalog.json')
meta={'v':1,'plugin':'1.15.0','meta':{'resources':[1000,100,100,100],'build_options':[],'blueprints':[],
 'buildings':[{'type':'kIdleFarm','lvl':0,'state':'kNormal'},{'type':'kDenseWheat','lvl':0,'state':'kNormal'}],
 'chars':[{'type':'kDefault','lvl':3,'state':'kWorking','work':'kIdleFarm','work_id':1,'harvest':{}},
 {'type':'kRecaller','lvl':3,'state':'kIdle','harvest':{},'harvest_bonus':{'kHarvestSpeed':0,'kMoreBuildPts':0}}]}}
snap={'v':1,'plugin':'1.15.0','seq':1,'game_version':'synthetic-smoke','screen_w':1280,'screen_h':720,'game_state':'kBase',
 'base':{'state':'kNormal','harvest_secs_left':1,'buildings':[
 {'id':1,'type':'kIdleFarm','x':3,'y':3,'tw':2,'th':2,'rot':0,'lvl':0,'state':'kNormal','stat':'kNum','worker':0,'range':2.5,'in_range':{'kDenseWheat':1}},
 {'id':2,'type':'kDenseWheat','x':3.5,'y':4.5,'tw':1,'th':1,'rot':0,'lvl':0,'state':'kNormal','stat':'kNum','cap':4,'res':4,'can_harvest':True}],
 'geo':{'left':0,'right':8,'bottom':0,'top':8,'space_w':1,'chunk_w':8,'chunk_h':8,'chunks':[[0,0]],'launcher':[3,.1],
 'worker_speed':5,'worker_speed_mult':1,'harvest_len':1,'colliders':[
 {'id':1,'shape':'box','pts':[[2,2],[4,2],[4,4],[2,4]]},
 {'id':2,'shape':'box','pts':[[3,4],[4,4],[4,5],[3,5]]}]}}}
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.CreateNamedPipeW.argtypes=[wt.LPCWSTR,wt.DWORD,wt.DWORD,wt.DWORD,wt.DWORD,wt.DWORD,wt.DWORD,ctypes.c_void_p];k.CreateNamedPipeW.restype=wt.HANDLE
k.ConnectNamedPipe.argtypes=[wt.HANDLE,ctypes.c_void_p];k.ConnectNamedPipe.restype=wt.BOOL
k.WriteFile.argtypes=[wt.HANDLE,ctypes.c_void_p,wt.DWORD,ctypes.POINTER(wt.DWORD),ctypes.c_void_p];k.WriteFile.restype=wt.BOOL
k.CloseHandle.argtypes=[wt.HANDLE]
pipe=k.CreateNamedPipeW('\\\\.\\pipe\\ballxpit-bridge-'+name,2|0x80000,0,1,65536,0,5000,None)
if pipe in (None,ctypes.c_void_p(-1).value):raise ctypes.WinError(ctypes.get_last_error())
stop=threading.Event();connected=threading.Event()
def send(value):
 payload=(json.dumps(value)+'\n').encode();buf=ctypes.create_string_buffer(payload);n=wt.DWORD()
 return bool(k.WriteFile(pipe,buf,len(payload),ctypes.byref(n),None))
def server():
 if not k.ConnectNamedPipe(pipe,None) and ctypes.get_last_error()!=535:return
 connected.set()
 if not send(meta):return
 while not stop.wait(.4):
  snap['seq']+=1
  if not send(snap):break
threading.Thread(target=server,daemon=True).start()
si=subprocess.STARTUPINFO();si.dwFlags|=subprocess.STARTF_USESHOWWINDOW;si.wShowWindow=0
stdout=(profile/'stdout.log').open('wb');stderr=(profile/'stderr.log').open('wb')
p=subprocess.Popen([str(exe),'--tray'],cwd=root,env=env,stdout=stdout,stderr=stderr,startupinfo=si,creationflags=subprocess.CREATE_NO_WINDOW)
try:
 deadline=time.monotonic()+45;passed=False;logtext=''
 while time.monotonic()<deadline and p.poll() is None:
  log=profile/'logs/companion.log'
  if log.exists():
   logtext=log.read_text(encoding='utf-8')
   if ' ERROR ' in logtext or 'Traceback' in logtext:raise RuntimeError(logtext[-6000:])
   if '\ubc30\uce58 \ucd94\ucc9c:' in logtext:passed=True;break
  time.sleep(.2)
 if not passed:raise RuntimeError('No frozen worker result; connected='+str(connected.is_set())+'; stderr='+ (profile/'stderr.log').read_text(encoding='utf-8',errors='replace')[-4000:])
 q=subprocess.run([str(exe),'--stop'],cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,startupinfo=si,creationflags=subprocess.CREATE_NO_WINDOW,timeout=15)
 p.wait(timeout=15)
 if p.returncode!=0 or q.returncode!=0:raise RuntimeError('Frozen helper did not stop cleanly.')
 if '\ub124\uc774\ud2f0\ube0c \uacc4\uc0b0 \uc0ac\uc6a9' not in logtext:raise RuntimeError('Bundled native module was not loaded.')
 print(json.dumps({'synthetic_pipe':connected.is_set(),'frozen_worker_completed':passed,'native_loaded':'\ub124\uc774\ud2f0\ube0c \uacc4\uc0b0 \uc0ac\uc6a9' in logtext,'exit':p.returncode,'stop_exit':q.returncode,'profile':str(profile)}))
finally:
 stop.set()
 if p.poll() is None:p.kill();p.wait()
 k.CloseHandle(pipe);stdout.close();stderr.close()

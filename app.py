import json, os, queue, shutil, subprocess, tempfile, threading, uuid, webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from PIL import ImageGrab, Image

VERSION = '0.1.0'

class App:
    def __init__(self, root):
        self.root = root
        root.title(f'CodexDesk v{VERSION}')
        root.geometry('880x760')
        self.events, self.callbacks, self.seq = queue.Queue(), {}, 0
        self.files, self.logged, self.busy = [], False, False
        self.temp = tempfile.TemporaryDirectory(prefix='codex-prototype-')
        self.out = tk.StringVar(value=str(Path.home() / 'Documents' / 'Codex輸出'))
        self.status = tk.StringVar(value='正在啟動 Codex…')
        bar = ttk.Frame(root); bar.pack(fill='x', padx=10, pady=5)
        ttk.Label(bar, textvariable=self.status).pack(side='left')
        ttk.Button(bar, text='登入 ChatGPT', command=self.login).pack(side='right')
        row = ttk.Frame(root); row.pack(fill='x', padx=10, pady=5)
        ttk.Label(row, text='模型：').pack(side='left')
        self.model = ttk.Combobox(row, state='readonly', width=26); self.model.pack(side='left')
        self.model.bind('<<ComboboxSelected>>', self.model_changed)
        ttk.Label(row, text='  推理程度：').pack(side='left')
        self.effort = ttk.Combobox(row, state='readonly', width=16); self.effort.pack(side='left')
        ttk.Button(row, text='重新讀取模型', command=self.load_models).pack(side='left', padx=8)
        self.models = []; self.model_pages = []
        row = ttk.Frame(root); row.pack(fill='x', padx=10, pady=5)
        ttk.Label(row, text='存取權限：').pack(side='left')
        self.access = ttk.Combobox(row, state='readonly', width=32, values=['僅讀取（無法產出檔案）', '工作資料夾可寫入（建議）', '完整電腦存取（請謹慎）'])
        self.access.current(1); self.access.pack(side='left')
        ttk.Label(row, text='執行動作時可能要求確認').pack(side='left', padx=8)
        row = ttk.Frame(root); row.pack(fill='x', padx=10)
        ttk.Label(row, text='輸出資料夾：').pack(side='left')
        ttk.Entry(row, textvariable=self.out).pack(side='left', fill='x', expand=True)
        ttk.Button(row, text='選擇', command=self.choose_output).pack(side='left')
        ttk.Label(root, text='輸入問題（Ctrl+V 可貼上圖片）').pack(anchor='w', padx=10, pady=(8,0))
        self.prompt = scrolledtext.ScrolledText(root, height=7, wrap='word'); self.prompt.pack(fill='x', padx=10)
        self.prompt.bind('<Control-v>', self.paste); self.prompt.bind('<Control-V>', self.paste)
        row = ttk.Frame(root); row.pack(fill='x', padx=10, pady=5)
        ttk.Button(row, text='加入附件', command=self.add_files).pack(side='left')
        ttk.Button(row, text='移除所選', command=self.remove).pack(side='left', padx=5)
        self.send_button = ttk.Button(row, text='送出新對話', command=self.send); self.send_button.pack(side='right')
        self.listbox = tk.Listbox(root, height=4); self.listbox.pack(fill='x', padx=10)
        ttk.Label(root, text='Codex 回覆').pack(anchor='w', padx=10, pady=(8,0))
        self.answer = scrolledtext.ScrolledText(root, wrap='word'); self.answer.pack(fill='both', expand=True, padx=10)
        row = ttk.Frame(root); row.pack(fill='x', padx=10, pady=8)
        ttk.Button(row, text='開啟輸出資料夾', command=self.open_output).pack(side='right')
        self.proc = None
        threading.Thread(target=self.start, daemon=True).start()
        root.after(100, self.poll); root.protocol('WM_DELETE_WINDOW', self.close)

    def start(self):
        try:
            exe = shutil.which('codex.cmd') or shutil.which('codex')
            if not exe: raise RuntimeError('找不到 Codex CLI。請先安裝 Codex 並確認 codex 指令可用。')
            self.proc = subprocess.Popen([exe, 'app-server'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', bufsize=1, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            threading.Thread(target=self.read_stdout, daemon=True).start()
            threading.Thread(target=lambda: [None for _ in self.proc.stderr], daemon=True).start()
            self.events.put(('ready', None))
        except Exception as e: self.events.put(('fatal', str(e)))

    def read_stdout(self):
        for line in self.proc.stdout:
            try: self.events.put(('msg', json.loads(line)))
            except json.JSONDecodeError: pass
        self.events.put(('fatal', 'Codex App Server 已結束。'))

    def request(self, method, params, callback):
        self.seq += 1; self.callbacks[self.seq] = callback
        self.write({'id': self.seq, 'method': method, 'params': params})
    def write(self, msg):
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False)+'\n'); self.proc.stdin.flush()
    def poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == 'ready':
                    self.request('initialize', {'clientInfo': {'name':'codexdesk','title':'CodexDesk','version':VERSION}}, self.initialized)
                elif kind == 'fatal': self.status.set(data); self.busy = False
                else: self.handle(data)
        except queue.Empty: pass
        self.root.after(100, self.poll)
    def initialized(self, msg):
        if 'error' in msg: self.status.set(str(msg['error'])); return
        self.write({'method':'initialized','params':{}})
        self.check_account()
    def check_account(self): self.request('account/read', {'refreshToken':False}, self.account)
    def account(self, msg):
        acc = msg.get('result', {}).get('account')
        self.logged = bool(acc and acc.get('type') == 'chatgpt')
        self.status.set('ChatGPT 已登入' if self.logged else '尚未以 ChatGPT 登入')
        if not self.logged: self.login()
        else: self.load_models()
    def load_models(self):
        if not self.proc or not self.logged: return
        self.model_pages = []
        self.request('model/list', {'limit':100, 'includeHidden':False}, self.models_received)
    def models_received(self, msg):
        if 'error' in msg:
            self.model.set(''); self.models = []; self.effort.set('')
            messagebox.showerror('讀取模型失敗', str(msg['error'])); return
        result = msg.get('result', {}); self.model_pages.extend(result.get('data', []))
        if result.get('nextCursor'):
            self.request('model/list', {'limit':100, 'includeHidden':False, 'cursor':result['nextCursor']}, self.models_received); return
        self.models = self.model_pages
        self.model['values'] = [m.get('displayName') or m.get('model') for m in self.models]
        if self.models:
            index = next((i for i,m in enumerate(self.models) if m.get('isDefault')), 0)
            self.model.current(index); self.model_changed()
        else: self.model.set(''); self.effort.set(''); self.status.set('沒有可用模型')
    def model_changed(self, event=None):
        index = self.model.current()
        if index < 0: return
        m = self.models[index]
        values = [e['reasoningEffort'] for e in m.get('supportedReasoningEfforts', [])]
        self.effort['values'] = values
        default = m.get('defaultReasoningEffort')
        self.effort.set(default if default in values else (values[0] if values else ''))
    def login(self):
        if self.proc: self.request('account/login/start', {'type':'chatgpt'}, self.login_started)
    def login_started(self, msg):
        if 'error' in msg: messagebox.showerror('登入失敗', str(msg['error'])); return
        url = msg.get('result', {}).get('authUrl')
        if url: self.status.set('請在瀏覽器完成登入'); webbrowser.open(url)
        else: messagebox.showerror('登入失敗', str(msg))
    def handle(self, msg):
        if 'id' in msg and 'method' not in msg:
            cb = self.callbacks.pop(msg['id'], None)
            if cb: cb(msg)
            return
        method, p = msg.get('method'), msg.get('params', {})
        if 'id' in msg:
            if 'requestApproval' in (method or ''):
                yes = messagebox.askyesno('Codex 請求許可', f'{method}\n\n{json.dumps(p, ensure_ascii=False, indent=2)}\n\n允許執行嗎？')
                self.write({'id':msg['id'], 'result':{'decision':'accept' if yes else 'decline'}})
            else: self.write({'id':msg['id'], 'error':{'code':-32601, 'message':'Unsupported request'}})
        elif method == 'account/login/completed':
            if p.get('success'): self.check_account()
            else: self.status.set('登入未完成：'+str(p.get('error')))
        elif method == 'item/agentMessage/delta': self.answer.insert('end', p.get('delta','')); self.answer.see('end')
        elif method == 'error':
            self.answer.insert('end', '\n執行訊息：' + json.dumps(p.get('error', p), ensure_ascii=False) + '\n'); self.answer.see('end')
        elif method == 'item/completed':
            item = p.get('item', {})
            if item.get('type') == 'commandExecution' and item.get('status') == 'failed':
                self.answer.insert('end', '\n工具執行失敗：' + str(item.get('aggregatedOutput') or item.get('command') or '') + '\n'); self.answer.see('end')
        elif method == 'turn/completed':
            self.busy = False; turn = p.get('turn', {}); self.status.set('完成：'+str(turn.get('status')))
            if turn.get('error'): self.answer.insert('end', '\n錯誤：'+str(turn['error']))

    def choose_output(self):
        path = filedialog.askdirectory()
        if path: self.out.set(path)
    def open_output(self):
        try: Path(self.out.get()).mkdir(parents=True, exist_ok=True); os.startfile(self.out.get())
        except Exception as e: messagebox.showerror('錯誤', str(e))
    def add_files(self):
        for path in filedialog.askopenfilenames(): self.add(path)
    def add(self, path):
        self.files.append(path); self.listbox.insert('end', path)
    def remove(self):
        for i in reversed(self.listbox.curselection()): self.listbox.delete(i); self.files.pop(i)
    def paste(self, event):
        try:
            image = ImageGrab.grabclipboard()
            if isinstance(image, Image.Image):
                path = Path(self.temp.name) / (uuid.uuid4().hex + '.png'); image.save(path, 'PNG'); self.add(str(path)); return 'break'
        except Exception as e: messagebox.showerror('貼上圖片失敗', str(e)); return 'break'
    def send(self):
        if self.busy: return
        if not self.logged: messagebox.showwarning('需要登入', '請先以 ChatGPT 帳號登入。'); return
        if self.model.current() < 0: messagebox.showwarning('需要模型', '請先讀取並選擇模型。'); return
        prompt = self.prompt.get('1.0','end').strip()
        if not prompt: messagebox.showwarning('請輸入問題', '請先輸入要送出的內容。'); return
        try:
            out = Path(self.out.get()).expanduser().resolve(); out.mkdir(parents=True, exist_ok=True)
            job = out / ('Codex任務_' + uuid.uuid4().hex[:8]); inputs = job / 'inputs'; outputs = job / 'outputs'
            inputs.mkdir(parents=True); outputs.mkdir()
            images, other = [], []
            for idx, src in enumerate(self.files):
                source = Path(src); dst = inputs / (str(idx+1)+'_'+source.name); shutil.copy2(source, dst)
                (images if source.suffix.lower() in ('.png','.jpg','.jpeg','.webp','.gif') else other).append(str(dst))
            self.job, self.images = job, images
            selected = self.models[self.model.current()]
            if images and 'image' not in selected.get('inputModalities', ['text','image']):
                raise ValueError('所選模型不支援圖片輸入，請選擇其他模型。')
            self.selected_model = selected.get('model') or selected['id']; self.selected_effort = self.effort.get()
            self.input_text = prompt + '\n\n附件路徑：\n' + '\n'.join(other) + '\n請將產出的檔案放在：' + str(outputs) + '\n回覆中請列出產出檔案的完整路徑。'
            self.busy = True; self.answer.delete('1.0','end'); self.status.set('正在建立新對話…')
            sandbox = ['read-only', 'workspace-write', 'danger-full-access'][self.access.current()]
            self.request('thread/start', {'cwd':str(job), 'approvalPolicy':'on-request', 'sandbox':sandbox}, self.thread_started)
        except Exception as e: self.busy = False; messagebox.showerror('無法開始', str(e))
    def thread_started(self, msg):
        if 'error' in msg: self.busy = False; messagebox.showerror('建立對話失敗', str(msg['error'])); return
        tid = msg['result']['thread']['id']
        items = [{'type':'text','text':self.input_text}] + [{'type':'localImage','path':p} for p in self.images]
        self.status.set('Codex 處理中…')
        params = {'threadId':tid,'input':items, 'model':self.selected_model}
        if self.selected_effort: params['effort'] = self.selected_effort
        self.request('turn/start', params, self.turn_started)
    def turn_started(self, msg):
        if 'error' in msg: self.busy = False; self.status.set('執行失敗'); messagebox.showerror('執行失敗', str(msg['error']))
    def close(self):
        if self.proc: self.proc.terminate()
        self.temp.cleanup(); self.root.destroy()

if __name__ == '__main__':
    root = tk.Tk(); App(root); root.mainloop()

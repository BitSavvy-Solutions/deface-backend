# app.py
import os
import re
import json
import uuid
import queue
import signal
import shutil
import threading
import subprocess
from flask import Flask, request, send_file, render_template, jsonify, Response, stream_with_context

app = Flask(__name__)

UPLOAD_FOLDER = '/tmp/videos'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

jobs = {}

ANSI_RE    = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
PROGRESS_RE = re.compile(
    r'(\d+)%\|.*?\|\s*(\d+)/(\d+)\s*\[([^<\]]+)<([^,\]]+),\s*([^\]]+)\]'
)


def cleanup_videos():
    shutil.rmtree(UPLOAD_FOLDER, ignore_errors=True)


def _on_shutdown(signum, frame):
    cleanup_videos()
    os._exit(0)


signal.signal(signal.SIGTERM, _on_shutdown)
signal.signal(signal.SIGINT,  _on_shutdown)


def strip_ansi(text):
    return ANSI_RE.sub('', text)


def parse_progress(line):
    line = strip_ansi(line)
    m = PROGRESS_RE.search(line)
    if not m:
        return None
    return {
        'type':      'progress',
        'percent':   int(m.group(1)),
        'current':   int(m.group(2)),
        'total':     int(m.group(3)),
        'elapsed':   m.group(4).strip(),
        'remaining': m.group(5).strip(),
        'speed':     m.group(6).strip(),
    }


def build_deface_command(input_path, output_path, options):
    cmd = ['deface', input_path, '-o', output_path]
    cmd += ['--thresh',     str(options.get('thresh',      '0.2'))]
    cmd += ['--mask-scale', str(options.get('mask_scale',  '1.3'))]

    scale = options.get('scale', '').strip()
    if scale:
        cmd += ['--scale', scale]

    if options.get('boxes')         == 'on': cmd.append('--boxes')
    if options.get('draw_scores')   == 'on': cmd.append('--draw-scores')
    if options.get('keep_audio')    == 'on': cmd.append('--keep-audio')
    if options.get('keep_metadata') == 'on': cmd.append('--keep-metadata')

    replace_with = options.get('replacewith', 'blur')
    cmd += ['--replacewith', replace_with]
    if replace_with == 'mosaic':
        cmd += ['--mosaicsize', str(options.get('mosaicsize', '20'))]

    cmd += ['--backend', options.get('backend', 'auto')]
    return cmd


def run_deface(job_id, cmd, output_path, filename):
    q = jobs.get(job_id)
    if q is None:
        return

    recent = []

    try:
        # Merge stderr into stdout so one pipe covers all output.
        # This avoids the deadlock risk of leaving an unread pipe full.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        buf = b''
        for ch in iter(lambda: proc.stdout.read(1), b''):
            if ch in (b'\r', b'\n'):
                line = buf.decode('utf-8', errors='replace').strip()
                buf  = b''
                if not line:
                    continue

                recent.append(line)
                if len(recent) > 30:
                    recent.pop(0)

                prog = parse_progress(line)
                if prog:
                    q.put(prog)
                else:
                    clean = strip_ansi(line)
                    if clean:
                        q.put({'type': 'log', 'message': clean})
            else:
                buf += ch

        # Anything left in the buffer after the pipe closes
        if buf:
            line = buf.decode('utf-8', errors='replace').strip()
            if line:
                prog = parse_progress(line)
                q.put(prog if prog else {'type': 'log', 'message': strip_ansi(line)})

        proc.wait()

        if proc.returncode == 0 and os.path.exists(output_path):
            q.put({'type': 'done', 'filename': filename})
        else:
            tail = '\n'.join(strip_ansi(l) for l in recent[-5:])
            q.put({'type': 'error', 'message': tail or 'Processing failed'})

    except Exception as exc:
        q.put({'type': 'error', 'message': str(exc)})
    finally:
        q.put(None)   # sentinel signals the SSE generator to stop


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/process', methods=['POST'])
def process_video():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    video_file = request.files['file']
    if video_file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    file_id      = str(uuid.uuid4())
    input_path   = f'{UPLOAD_FOLDER}/{file_id}_input.mp4'
    output_path  = f'{UPLOAD_FOLDER}/{file_id}_output.mp4'
    out_filename = f'{file_id}_output.mp4'

    video_file.save(input_path)

    options = {
        'thresh':        request.form.get('thresh',        '0.2'),
        'scale':         request.form.get('scale',         ''),
        'boxes':         request.form.get('boxes',         ''),
        'draw_scores':   request.form.get('draw_scores',   ''),
        'mask_scale':    request.form.get('mask_scale',    '1.3'),
        'replacewith':   request.form.get('replacewith',   'blur'),
        'mosaicsize':    request.form.get('mosaicsize',    '20'),
        'keep_audio':    request.form.get('keep_audio',    ''),
        'backend':       request.form.get('backend',       'auto'),
        'keep_metadata': request.form.get('keep_metadata', ''),
    }

    cmd = build_deface_command(input_path, output_path, options)

    # API callers get a synchronous response with the file directly
    if 'application/octet-stream' in request.headers.get('Accept', ''):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not os.path.exists(output_path):
            return jsonify({'error': 'Processing failed', 'details': result.stderr}), 500
        return send_file(output_path, mimetype='video/mp4', as_attachment=True,
                         download_name='anonymized.mp4')

    # Browser callers get a job_id and stream progress via SSE
    job_id = str(uuid.uuid4())
    jobs[job_id] = queue.Queue()

    threading.Thread(
        target=run_deface,
        args=(job_id, cmd, output_path, out_filename),
        daemon=True
    ).start()

    return jsonify({'job_id': job_id})


@app.route('/progress/<job_id>')
def progress_stream(job_id):
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404

    def generate():
        q = jobs.get(job_id)
        if q is None:
            return
        try:
            while True:
                try:
                    item = q.get(timeout=25)
                except queue.Empty:
                    yield 'data: {"type":"ping"}\n\n'
                    continue
                if item is None:
                    break
                yield f'data: {json.dumps(item)}\n\n'
        finally:
            jobs.pop(job_id, None)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )


@app.route('/result/<filename>')
def result_page(filename):
    if not re.match(r'^[0-9a-f-]+_output\.mp4$', filename):
        return jsonify({'error': 'Invalid filename'}), 400
    if not os.path.exists(f'{UPLOAD_FOLDER}/{filename}'):
        return jsonify({'error': 'File not found'}), 404
    return render_template('result.html', filename=filename)


@app.route('/video/<filename>')
def serve_video(filename):
    file_path = f'{UPLOAD_FOLDER}/{filename}'
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    return send_file(file_path, mimetype='video/mp4', as_attachment=True,
                     download_name='anonymized.mp4')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
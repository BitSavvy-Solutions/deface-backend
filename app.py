import os
import uuid
import subprocess
from flask import Flask, request, send_file, render_template, jsonify

app = Flask(__name__)

UPLOAD_FOLDER = '/tmp/videos'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def build_deface_command(input_path, output_path, options):
    cmd = ['deface', input_path, '-o', output_path]

    cmd += ['--thresh', str(options.get('thresh', '0.2'))]
    cmd += ['--mask-scale', str(options.get('mask_scale', '1.3'))]

    scale = options.get('scale', '').strip()
    if scale:
        cmd += ['--scale', scale]

    if options.get('boxes') == 'on':
        cmd.append('--boxes')

    if options.get('draw_scores') == 'on':
        cmd.append('--draw-scores')

    if options.get('keep_audio') == 'on':
        cmd.append('--keep-audio')

    if options.get('keep_metadata') == 'on':
        cmd.append('--keep-metadata')

    replace_with = options.get('replacewith', 'blur')
    cmd += ['--replacewith', replace_with]

    if replace_with == 'mosaic':
        cmd += ['--mosaicsize', str(options.get('mosaicsize', '20'))]

    backend = options.get('backend', 'auto')
    cmd += ['--backend', backend]

    return cmd

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

    file_id = str(uuid.uuid4())
    input_path = f'{UPLOAD_FOLDER}/{file_id}_input.mp4'
    output_path = f'{UPLOAD_FOLDER}/{file_id}_output.mp4'

    video_file.save(input_path)

    options = {
        'thresh':        request.form.get('thresh', '0.2'),
        'scale':         request.form.get('scale', ''),
        'boxes':         request.form.get('boxes', ''),
        'draw_scores':   request.form.get('draw_scores', ''),
        'mask_scale':    request.form.get('mask_scale', '1.3'),
        'replacewith':   request.form.get('replacewith', 'blur'),
        'mosaicsize':    request.form.get('mosaicsize', '20'),
        'keep_audio':    request.form.get('keep_audio', ''),
        'backend':       request.form.get('backend', 'auto'),
        'keep_metadata': request.form.get('keep_metadata', ''),
    }

    cmd = build_deface_command(input_path, output_path, options)
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0 or not os.path.exists(output_path):
        return jsonify({'error': 'Processing failed', 'details': result.stderr}), 500

    is_api = 'application/octet-stream' in request.headers.get('Accept', '')
    if is_api:
        return send_file(output_path, mimetype='video/mp4', as_attachment=True,
                         download_name='anonymized.mp4')

    return render_template('result.html', filename=f'{file_id}_output.mp4')

@app.route('/video/<filename>')
def serve_video(filename):
    file_path = f'{UPLOAD_FOLDER}/{filename}'
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    return send_file(file_path, mimetype='video/mp4', as_attachment=True,
                     download_name='anonymized.mp4')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
"""
EYBC Tournament Scheduler — Flask Server

Serves the scheduler UI (index.html) and provides an API endpoint
for CP-SAT optimal schedule generation.

Usage:
  pip install -r requirements.txt
  python app.py
  Open http://localhost:5000
"""

from flask import Flask, send_from_directory, request, jsonify
from scheduler import solve_schedule
import os

app = Flask(__name__, static_folder='.', static_url_path='')


@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/api/generate', methods=['POST'])
def generate():
    """
    Accepts tournament config JSON, runs CP-SAT solver, returns schedule.
    The frontend POSTs the same config it would use for local generation.
    """
    try:
        config = request.get_json()
        if not config:
            return jsonify({'error': 'No config provided'}), 400

        time_limit = config.get('solverTimeLimit', 120)
        result = solve_schedule(config, time_limit=time_limit)

        if 'error' in result:
            return jsonify(result), 422

        return jsonify(result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/health')
def health():
    """Health check — frontend uses this to detect if the server is running."""
    return jsonify({'status': 'ok', 'solver': 'cp-sat'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"\n  EYBC Tournament Scheduler")
    print(f"  CP-SAT Optimal Solver")
    print(f"  Open http://localhost:{port}\n")
    app.run(host='0.0.0.0', port=port, debug=False)

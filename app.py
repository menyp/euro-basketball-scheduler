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
from validator import validate_schedule
import scheduler as _scheduler_module
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


@app.route('/api/validate', methods=['POST'])
def validate():
    """
    Check a manually-edited schedule against the original setup.

    Body: {config, games, originalGames} — `config` is the same shape
    /api/generate receives (the rulebook, loaded from the original JSON);
    `games` is the flat schedule rebuilt from the edited Excel; `originalGames`
    (optional) is the original solver schedule, used for the health comparison.
    Does not re-solve; see validator.py.
    """
    try:
        body = request.get_json()
        if not body or 'config' not in body or 'games' not in body:
            return jsonify({'error': 'Expected JSON body with {config, games}'}), 400

        result = validate_schedule(body['config'], body['games'],
                                   original_games=body.get('originalGames'))
        return jsonify(result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/health')
def health():
    """Health check — frontend uses this to detect if the server is running."""
    return jsonify({'status': 'ok', 'solver': 'cp-sat'})


@app.route('/api/progress')
def progress():
    """Live solver progress. Polled by the UI banner while /api/generate is
    in flight. Returns the shared dict scheduler._progress_state."""
    # Copy to avoid any concurrent mutation mid-serialize.
    return jsonify(dict(_scheduler_module._progress_state))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"\n  EYBC Tournament Scheduler")
    print(f"  CP-SAT Optimal Solver")
    print(f"  Open http://localhost:{port}\n")
    # threaded=True so /api/progress can be served while /api/generate is
    # running (otherwise the progress poll blocks behind the solver).
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)

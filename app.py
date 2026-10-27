"""TriageLoop — Emergency Care Intelligence (simulation).

Run:  python app.py   then open http://127.0.0.1:5000
Admin commands:  flask --app app init-db | create-user NAME --role admin | load-demo
"""
import os

from web import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host=os.environ.get("TRIAGELOOP_HOST", "127.0.0.1"),
            port=int(os.environ.get("TRIAGELOOP_PORT", "5000")),
            debug=os.environ.get("FLASK_DEBUG") == "1" and not app.config["PRODUCTION"],
            use_reloader=False)

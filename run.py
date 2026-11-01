from waitress import serve
from s3_audio_explorer import create_app

if __name__ == "__main__":
    app = create_app()
    try:
        print(f"S3 Audio Explorer : http://{app.config['HOST']}:{app.config['PORT']}")
        serve(app, host=app.config["HOST"], port=app.config["PORT"], threads=8)
    finally:
        app.extensions["transfers"].shutdown()

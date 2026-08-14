"""
WSGI entry point.

Used by PythonAnywhere (and any other WSGI host, e.g. gunicorn).
PythonAnywhere's "Web" tab wants a module-level variable called
`application`, which is exactly what `create_app()` returns.
"""

from app import create_app

application = create_app()

if __name__ == "__main__":
    application.run(debug=True)

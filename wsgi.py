"""Punto de entrada WSGI (gunicorn/Passenger/mod_wsgi): wsgi:app"""
from ajsystem import create_app

app = create_app()
application = app  # nombre esperado por Passenger / mod_wsgi

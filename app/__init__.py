"""AIDC Workload Matcher; importing the package never opens a database."""


def create_app(config=None):
    """Flask application factory, imported lazily for existing CLI compatibility."""
    from app.web import create_app as factory
    return factory(config)

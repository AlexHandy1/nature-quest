def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "eval: makes real network calls to third-party APIs (OpenRouter, GBIF), "
        "costs money, excluded by default. Run explicitly: pytest -m eval.",
    )

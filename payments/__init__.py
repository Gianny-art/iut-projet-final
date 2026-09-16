from .base import PaymentResult, PaymentProvider
from .manual import ManualProvider
from .campay import CampayProvider
from .maviance import MavianceProvider

_PROVIDERS = {}


def _instance(name):
    if name not in _PROVIDERS:
        if name == 'campay':
            _PROVIDERS[name] = CampayProvider()
        elif name == 'maviance':
            _PROVIDERS[name] = MavianceProvider()
        else:
            _PROVIDERS[name] = ManualProvider()
    return _PROVIDERS[name]


def get_provider(name):
    """Return a provider instance for the given name, falling back to manual
    if the provider isn't configured (missing credentials)."""
    provider = _instance(name)
    if name in ('campay', 'maviance') and not provider.is_configured():
        return _instance('manual')
    return provider


def available_providers():
    """List provider names that have real credentials configured."""
    names = ['manual']
    if _instance('campay').is_configured():
        names.append('campay')
    if _instance('maviance').is_configured():
        names.append('maviance')
    return names

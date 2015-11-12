from django.core.exceptions import PermissionDenied
from django.contrib.auth.signals import user_logged_out
from django.contrib.auth import login, load_backend, BACKEND_SESSION_KEY
from django.dispatch import receiver
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils.http import is_safe_url

from compat import get_user_model, import_string
from compat import resolve_url

from hijack import settings as hijack_settings
from hijack.signals import post_superuser_login
from hijack.signals import post_superuser_logout


def get_used_backend(request):
    backend_str = request.session[BACKEND_SESSION_KEY]
    backend = load_backend(backend_str)
    return backend


def release_hijack(request):
    hijack_history = request.session.get('hijack_history', False)

    if not hijack_history:
        raise PermissionDenied

    if hijack_history:
        user_pk = hijack_history.pop()
        user = get_object_or_404(get_user_model(), pk=user_pk)
        backend = get_used_backend(request)
        user.backend = "%s.%s" % (backend.__module__,
                                  backend.__class__.__name__)
        login(request, user)
    if hijack_history:
        request.session['hijack_history'] = hijack_history
        request.session['is_hijacked_user'] = True
        request.session['display_hijack_warning'] = True
    else:
        try:
            del request.session['hijack_history']
            del request.session['is_hijacked_user']
            del request.session['display_hijack_warning']
        except KeyError:
            pass
    request.session.modified = True
    return redirect_to_next(request, default_url=hijack_settings.HIJACK_LOGOUT_REDIRECT_URL)


def is_authorized(hijacker, hijacked):
    """Checks if the user has the correct permission to Hijack another user.

    By default only superusers are allowed to hijack.

    An exception is made to allow staff members to hijack when
    HIJACK_AUTHORIZE_STAFF is enabled in the Django settings.

    By default it prevents staff users from hijacking other staff users.
    This can be disabled by enabling the HIJACK_AUTHORIZE_STAFF_TO_HIJACK_STAFF
    setting in the Django settings.

    Staff users can never hijack superusers. Also, hijacked users must have is_active==True.
    """
    if not hijacked.is_active:
        return False

    if hijacked.is_superuser and not hijacker.is_superuser:
        return False

    if hijacker.is_superuser:
        return True

    if hijacker.is_staff and hijack_settings.HIJACK_AUTHORIZE_STAFF:
        if hijacked.is_staff and not hijack_settings.HIJACK_AUTHORIZE_STAFF_TO_HIJACK_STAFF:
            return False
        return True

    return False


def check_hijack_authorization(request, user):
    check_authorization = import_string(hijack_settings.HIJACK_AUTHORIZATION_CHECK)
    is_authorized = check_authorization(request.user, user)
    if not is_authorized:
        raise PermissionDenied


def login_user(request, user):
    ''' hijack mechanism '''
    hijack_history = [request.user._meta.pk.value_to_string(request.user)]
    if request.session.get('hijack_history'):
        hijack_history = request.session['hijack_history'] + hijack_history

    check_hijack_authorization(request, user)

    backend = get_used_backend(request)
    user.backend = "%s.%s" % (backend.__module__, backend.__class__.__name__)
    last_login = user.last_login  # Save last_login to reset it after hijack login
    login(request, user)
    user.last_login = last_login
    user.save()
    post_superuser_login.send(sender=None, user_id=user.pk)
    request.session['hijack_history'] = hijack_history
    request.session['is_hijacked_user'] = True
    request.session['display_hijack_warning'] = True
    request.session.modified = True
    return redirect_to_next(request, default_url=hijack_settings.HIJACK_LOGIN_REDIRECT_URL)


@receiver(user_logged_out)
def logout_user(sender, **kwargs):
    ''' wraps logout signal '''
    user = kwargs['user']
    if hasattr(user, 'id'):
        post_superuser_logout.send(sender=None, user_id=user.pk)

def redirect_to_next(request, default_url=hijack_settings.HIJACK_LOGIN_REDIRECT_URL):
    redirect_to = request.GET.get('next', '')
    if not is_safe_url(redirect_to):
        redirect_to = default_url
    return HttpResponseRedirect(resolve_url(redirect_to))

from django.conf import settings


class ProxyClientIPMiddleware:
    """
    Behind Nginx every request comes from 127.0.0.1; Nginx passes the real address in X-Real-IP
    (overwriting anything the browser sent). Put it back in REMOTE_ADDR so the audit log and the
    login lockout see the person's own IP. Only on when NETCORE_BEHIND_PROXY is set, because
    without a proxy in front the header would be whatever the client chose.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.BEHIND_PROXY:
            real = request.META.get("HTTP_X_REAL_IP", "").strip()
            if real:
                request.META["REMOTE_ADDR"] = real
        return self.get_response(request)

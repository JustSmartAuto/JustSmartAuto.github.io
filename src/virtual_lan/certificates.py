from __future__ import annotations

import ctypes

from .resources import resource

X509_ASN_ENCODING = 1
CERT_STORE_ADD_REPLACE_EXISTING = 3


def install_trusted_certificate() -> None:
    data = resource("JustSmart-VirtualLanPy.cer").read_bytes()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt32.CertOpenSystemStoreW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    crypt32.CertOpenSystemStoreW.restype = ctypes.c_void_p
    crypt32.CertAddEncodedCertificateToStore.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
        ctypes.c_uint32, ctypes.c_void_p,
    ]
    crypt32.CertAddEncodedCertificateToStore.restype = ctypes.c_int
    crypt32.CertCloseStore.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    crypt32.CertCloseStore.restype = ctypes.c_int
    for store_name in ("Root", "TrustedPublisher"):
        store = crypt32.CertOpenSystemStoreW(None, store_name)
        if not store:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not crypt32.CertAddEncodedCertificateToStore(
                store, X509_ASN_ENCODING, data, len(data),
                CERT_STORE_ADD_REPLACE_EXISTING, None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            crypt32.CertCloseStore(store, 0)

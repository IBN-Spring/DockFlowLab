from src.utils.path import get_path

PUBLIC_KEY_STR = """
-----BEGIN RSA PUBLIC KEY-----
MIIBCgKCAQEAgjvaOHhjnxD8Q1EDeq3Aprp/XFgPk7KAzEVFlNk3/m2dLr3FnYqI
LpLH6mn/Ky9mbkWYuUdebxtvZcgD8WA9Xk/pjbuLx5XlFtCug3aKs8DGnttnekLB
TAo14ZyK+2df++cSFnPUi7VNFMGn/uHrfTScgb1lCNoW7OBx6NLh+Zt+ik3h8vj3
+66oFRxCnGVW49P5p+kORJLc4lnjE1b4BYDttuN0vhhuOGSgDvAj0Uat2YUM6MWo
fRfQQYrR+t236YjhV8b72uJ0NlbXKKTO9Bp4lR8p5oyekf6yXSWdqWFQUn+ivQcp
tOHTL0fpkPGK0Ie7CFvoysBJjEOX9vhDCwIDAQAB
-----END RSA PUBLIC KEY-----
"""
LICENSE_KEY_PATH = get_path("license.key")
LICENSE_CACHE_PATH = get_path("license_cache.json")
VALIDATE_INTERVAL_SECONDS = 60
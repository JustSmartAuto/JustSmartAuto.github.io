NETWORK_NAME = "justsmart"
NETWORK_SECRET = "justsmart"
VIRTUAL_CIDR = "10.168.10.0/24"
PUBLIC_SERVER = "tcp://public.easytier.cn:11010"
INSTANCE_NAME = "justsmart"

CORE_ARGS = (
    "--dhcp",
    "--ipv4", VIRTUAL_CIDR,
    "--network-name", NETWORK_NAME,
    "--network-secret", NETWORK_SECRET,
    "--peers", PUBLIC_SERVER,
    "--instance-name", INSTANCE_NAME,
    "--no-listener",
    "--multi-thread",
)

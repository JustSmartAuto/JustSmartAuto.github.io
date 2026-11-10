NETWORK_NAME = "justsmart"
NETWORK_SECRET = "justsmart"
VIRTUAL_CIDR = "10.168.10.0/24"
PUBLIC_SERVER = "tcp://easytier-us.slarker.me:11010"
PUBLIC_SERVER_UDP = "udp://easytier-us.slarker.me:11010"
INSTANCE_NAME = "justsmart"

CORE_ARGS = (
    "--dhcp",
    "--ipv4", VIRTUAL_CIDR,
    "--network-name", NETWORK_NAME,
    "--network-secret", NETWORK_SECRET,
    "--peers", PUBLIC_SERVER, PUBLIC_SERVER_UDP,
    "--listeners", "tcp://0.0.0.0:0", "udp://0.0.0.0:0", "wg://0.0.0.0:0",
    "--instance-name", INSTANCE_NAME,
)

from typing import Optional, TypedDict


class State(TypedDict):
    Status: str
    Running: bool
    Paused: bool
    Restarting: bool
    OOMKilled: bool
    Dead: bool
    Pid: int
    ExitCode: int
    Error: str
    StartedAt: str
    FinishedAt: str


class LogConfig(TypedDict):
    Type: str
    Config: dict


class RestartPolicy(TypedDict):
    Name: str
    MaximumRetryCount: int


class PortBinding(TypedDict):
    # Assuming more specific details may be added later
    pass


class VolumeOptions(TypedDict):
    # Assuming more specific details may be added later
    pass


class VolumeMount(TypedDict):
    Type: str
    Source: str
    Target: str
    VolumeOptions: VolumeOptions


class HostConfig(TypedDict):
    Binds: Optional[None]
    ContainerIDFile: str
    LogConfig: LogConfig
    NetworkMode: str
    PortBindings: dict
    RestartPolicy: RestartPolicy
    AutoRemove: bool
    VolumeDriver: str
    VolumesFrom: Optional[None]
    ConsoleSize: list[int]
    CapAdd: Optional[None]
    CapDrop: Optional[None]
    CgroupnsMode: str
    Dns: Optional[None]
    DnsOptions: Optional[None]
    DnsSearch: Optional[None]
    ExtraHosts: list[str]
    GroupAdd: Optional[None]
    IpcMode: str
    Cgroup: str
    Links: Optional[None]
    OomScoreAdj: int
    PidMode: str
    Privileged: bool
    PublishAllPorts: bool
    ReadonlyRootfs: bool
    SecurityOpt: Optional[None]
    UTSMode: str
    UsernsMode: str
    ShmSize: int
    Runtime: str
    Isolation: str
    CpuShares: int
    Memory: int
    NanoCpus: int
    CgroupParent: str
    BlkioWeight: int
    BlkioWeightDevice: Optional[None]
    BlkioDeviceReadBps: Optional[None]
    BlkioDeviceWriteBps: Optional[None]
    BlkioDeviceReadIOps: Optional[None]
    BlkioDeviceWriteIOps: Optional[None]
    CpuPeriod: int
    CpuQuota: int
    CpuRealtimePeriod: int
    CpuRealtimeRuntime: int
    CpusetCpus: str
    CpusetMems: str
    Devices: Optional[None]
    DeviceCgroupRules: Optional[None]
    DeviceRequests: Optional[None]
    MemoryReservation: int
    MemorySwap: int
    MemorySwappiness: Optional[None]
    OomKillDisable: Optional[None]
    PidsLimit: Optional[None]
    Ulimits: Optional[None]
    CpuCount: int
    CpuPercent: int
    IOMaximumIOps: int
    IOMaximumBandwidth: int
    Mounts: list[VolumeMount]
    MaskedPaths: list[str]
    ReadonlyPaths: list[str]


class GraphDriverData(TypedDict):
    LowerDir: str
    MergedDir: str
    UpperDir: str
    WorkDir: str


class GraphDriver(TypedDict):
    Data: GraphDriverData
    Name: str


class Mount(TypedDict):
    Type: str
    Name: str
    Source: str
    Destination: str
    Driver: str
    Mode: str
    RW: bool
    Propagation: str


class ConfigLabels(TypedDict):
    # Assuming more specific details may be added later
    pass


class Config(TypedDict):
    Hostname: str
    Domainname: str
    User: str
    AttachStdin: bool
    AttachStdout: bool
    AttachStderr: bool
    ExposedPorts: dict
    Tty: bool
    OpenStdin: bool
    StdinOnce: bool
    Env: list[str]
    Cmd: list[str]
    Image: str
    Volumes: Optional[None]
    WorkingDir: str
    Entrypoint: list[str]
    OnBuild: Optional[None]
    Labels: ConfigLabels


class NetworkSettingsNetwork(TypedDict):
    IPAMConfig: dict
    Links: Optional[None]
    Aliases: list[str]
    NetworkID: str
    EndpointID: str
    Gateway: str
    IPAddress: str
    IPPrefixLen: int
    IPv6Gateway: str
    GlobalIPv6Address: str
    GlobalIPv6PrefixLen: int
    MacAddress: str
    DriverOpts: Optional[None]


class NetworkSettings(TypedDict):
    Bridge: str
    SandboxID: str
    HairpinMode: bool
    LinkLocalIPv6Address: str
    LinkLocalIPv6PrefixLen: int
    Ports: dict
    SandboxKey: str
    SecondaryIPAddresses: Optional[None]
    SecondaryIPv6Addresses: Optional[None]
    EndpointID: str
    Gateway: str
    GlobalIPv6Address: str
    GlobalIPv6PrefixLen: int
    IPAddress: str
    IPPrefixLen: int
    IPv6Gateway: str
    MacAddress: str
    Networks: dict


class DockerContainer(TypedDict):
    Id: str
    Created: str
    Path: str
    Args: list[str]
    State: State
    Image: str
    ResolvConfPath: str
    HostnamePath: str
    HostsPath: str
    LogPath: str
    Name: str
    RestartCount: int
    Driver: str
    Platform: str
    MountLabel: str
    ProcessLabel: str
    AppArmorProfile: str
    ExecIDs: Optional[None]
    HostConfig: HostConfig
    GraphDriver: GraphDriver
    Mounts: list[Mount]
    Config: Config
    NetworkSettings: NetworkSettings

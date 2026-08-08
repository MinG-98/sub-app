# Building the binaries the agent depends on

The agent needs two things it cannot build itself: a sing-box with the V2Ray
API compiled in, and the small gRPC stats helper.

## sing-box with `with_v2ray_api`

Release builds of sing-box do **not** include `with_v2ray_api`, so per-user
traffic counters are unavailable with a stock binary.  It has to be rebuilt.

    sing-box  1.13.14
    Go        1.25.11

```sh
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
  -trimpath \
  -ldflags "-X 'github.com/sagernet/sing-box/constant.Version=1.13.14' \
            -X internal/godebug.defaultGODEBUG=multipathtcp=0 \
            -checklinkname=0 -s -w -buildid=" \
  -tags 'with_gvisor,with_quic,with_dhcp,with_wireguard,with_utls,with_acme,with_clash_api,with_tailscale,with_ccm,with_ocm,with_naive_outbound,badlinkname,tfogo_checklinkname0,with_purego,with_v2ray_api' \
  -o sing-box ./cmd/sing-box
```

Set `GOARCH=arm64` for arm nodes; nothing else changes.

**Build static (`CGO_ENABLED=0`).**  One binary then runs on both glibc and musl
hosts, which matters because the fleet mixes Debian and Alpine.  A dynamically
linked build needs a `libgcompat` shim on Alpine and is not worth the trouble.

The tag list beyond `with_v2ray_api` matches what the existing deployment was
already built with — keep it identical so a rebuilt binary stays a drop-in
replacement rather than silently dropping a feature some node relies on.

### Reproducibility

`-trimpath` plus `-buildid=` makes the output byte-identical across rebuilds on
the same toolchain.  Verified by rebuilding twice and comparing:

    amd64 static   364eaca5db3d7f8c7fc45e223f2b235fd07761c5c463d4ccefa184b8800a116e

Always check the hash after transferring a binary to a node.  **Do not trust
file size** — a resumed `curl -C -` download once produced a file of exactly the
right length whose hash differed, which would have installed a corrupt proxy
core into production.

## Stats helper

```sh
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
  -trimpath -ldflags '-s -w -buildid=' \
  -o sub-app-vless-stats ./vless-stats.go
```

    amd64 static   389a070f1f2c91531509e96f4dad5a52f98433e63989b957b3321e487d863abd

Install to `/usr/local/libexec/sub-app-vless-stats`.  The center's
`proxy_collector` looks there too, overridable via
`SUB_APP_VLESS_STATS_BINARY`.

The helper imports `github.com/sagernet/sing-box/experimental/v2rayapi`, so
build it from inside the sing-box source tree.

## Installing on small nodes

Several nodes run in containers with a 128 MB cgroup limit.  Copying a 61 MB
binary with `install` or `cp` there **drops the SSH session**: the read plus the
write pushes page cache to the cgroup limit and reclaim kills the connection.
It is not OOM — `memory.events` shows `oom_kill 0`.

Stream it straight to the destination instead, bypassing page cache:

```sh
curl -fsS <url> --max-time 900 | dd of=/usr/local/bin/sing-box bs=1M oflag=direct
chmod 755 /usr/local/bin/sing-box
sync
sha256sum /usr/local/bin/sing-box
```

On links slow enough that a single transfer may outlast the SSH session, run it
under `nohup` so a dropped connection does not abort the download.

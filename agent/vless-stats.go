// sub-app-vless-stats queries the loopback V2Ray API of a sing-box instance
// and prints per-user traffic counters as JSON.
//
// It exists so the node agent can read VLESS user statistics without speaking
// gRPC itself.  Output shape matches what the center's proxy collector already
// parses:
//
//	{"stats":[{"name":"user>>>ID>>>traffic>>>uplink","value":123}, ...]}
//
// The endpoint must stay on 127.0.0.1; this tool never binds a port.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/sagernet/sing-box/experimental/v2rayapi"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

type stat struct {
	Name  string `json:"name"`
	Value int64  `json:"value"`
}

type output struct {
	Stats []stat `json:"stats"`
}

func main() {
	addr := flag.String("addr", "127.0.0.1:10085", "V2Ray API address (loopback only)")
	pattern := flag.String("pattern", "", "stat name filter")
	reset := flag.Bool("reset", false, "reset counters after reading")
	timeout := flag.Duration("timeout", 5*time.Second, "request timeout")
	flag.Parse()

	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()

	conn, err := grpc.NewClient(*addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		fmt.Fprintln(os.Stderr, "dial failed")
		os.Exit(1)
	}
	defer conn.Close()

	// sing-box registers the stats service under the upstream V2Ray name on
	// some builds and under its own package name on others.  The request and
	// response messages are wire-compatible, so try both paths rather than
	// pinning one and failing on the other.
	methods := []string{
		"/v2ray.core.app.stats.command.StatsService/QueryStats",
		"/experimental.v2rayapi.StatsService/QueryStats",
	}
	request := &v2rayapi.QueryStatsRequest{Pattern: *pattern, Reset_: *reset}
	response := &v2rayapi.QueryStatsResponse{}
	var lastErr error
	for _, method := range methods {
		lastErr = conn.Invoke(ctx, method, request, response)
		if lastErr == nil {
			break
		}
	}
	if lastErr != nil {
		fmt.Fprintln(os.Stderr, "query failed:", lastErr)
		os.Exit(1)
	}

	result := output{Stats: make([]stat, 0, len(response.GetStat()))}
	for _, item := range response.GetStat() {
		result.Stats = append(result.Stats, stat{Name: item.GetName(), Value: item.GetValue()})
	}
	if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
		fmt.Fprintln(os.Stderr, "encode failed")
		os.Exit(1)
	}
}

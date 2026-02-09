#!/bin/bash

CONTROLLER="localhost:8181"
AUTH="admin:admin"


# Function to get the ACTUAL topology data
get_topology() {
    curl -s -u "$AUTH" "http://$CONTROLLER/restconf/operational/network-topology:network-topology/" | \
        jq -r '."network-topology".topology[] | select(."topology-id" == "flow:1")' 2>/dev/null
}

# DIRECT TEST: Show exactly what's in the topology
show_topology_debug() {
    echo "=== DEBUG: Topology Structure ==="
    echo ""
    
    TOPOLOGY=$(get_topology)
    
    if [ -z "$TOPOLOGY" ] || [ "$TOPOLOGY" = "null" ]; then
        echo "ERROR: No topology found with ID 'flow:1'"
        echo ""
        echo "Raw response:"
        curl -s -u "$AUTH" "http://$CONTROLLER/restconf/operational/network-topology:network-topology/" | head -50
        return
    fi
    
    echo "1. Topology ID: $(echo "$TOPOLOGY" | jq -r '."topology-id"')"
    echo ""
    
    echo "2. All Nodes:"
    echo "$TOPOLOGY" | jq -r '.node[]? | "  \(."node-id")"' 2>/dev/null
    echo ""
    
    echo "3. Host Nodes (with MAC addresses):"
    echo "$TOPOLOGY" | jq -r '
        .node[]? |
        select(."host-tracker-service:addresses"?) |
        ."node-id" as $node |
        ."host-tracker-service:addresses"[]? |
        "  Node: \($node) | MAC: \(.mac // "none") | IP: \(."ip" // "none")"' 2>/dev/null
    echo ""
    
    echo "4. Switch Nodes:"
    echo "$TOPOLOGY" | jq -r '
        .node[]? |
        select(."host-tracker-service:addresses"? | not) |
        "  \(."node-id")"' 2>/dev/null
    echo ""
    
    echo "5. Links (first 10):"
    echo "$TOPOLOGY" | jq -r '.link[]? | "  \(."source"."source-node")[\(."source"."source-tp")] → \(."destination"."dest-node")[\(."destination"."dest-tp")]"' 2>/dev/null | head -10
}

# SIMPLE function to find a host
find_host_simple() {
    local identifier="$1"
    local TOPOLOGY=$(get_topology)
    
    if [ -z "$TOPOLOGY" ] || [ "$TOPOLOGY" = "null" ]; then
        echo ""
        return
    fi
    
    # Try MAC address
    if [[ "$identifier" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
        echo "$TOPOLOGY" | jq -r '
            .node[]? |
            select(."host-tracker-service:addresses"? and 
                   (."host-tracker-service:addresses"[]?.mac? == "'"$identifier"'")) |
            ."node-id" as $node |
            ."host-tracker-service:addresses"[0]? as $addr |
            "\($node)|\($addr.mac // "")|\($addr."ip" // "")"' 2>/dev/null | head -1
    # Try IP address
    elif [[ "$identifier" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "$TOPOLOGY" | jq -r '
            .node[]? |
            select(."host-tracker-service:addresses"? and 
                   (."host-tracker-service:addresses"[]?."ip"? == "'"$identifier"'")) |
            ."node-id" as $node |
            ."host-tracker-service:addresses"[0]? as $addr |
            "\($node)|\($addr.mac // "")|\($addr."ip" // "")"' 2>/dev/null | head -1
    else
        echo ""
    fi
}

# WORKING trace function
trace_path_working() {
    local src="$1"
    local dst="$2"
    
    echo "Tracing path from $src to $dst..."
    echo "----------------------------------------"
    echo ""
    
    # First, show debug info
    show_topology_debug
    echo ""
    
    echo "Looking for hosts..."
    echo ""
    
    SRC_INFO=$(find_host_simple "$src")
    DST_INFO=$(find_host_simple "$dst")
    
    if [ -n "$SRC_INFO" ] && [ -n "$DST_INFO" ]; then
        SRC_NODE=$(echo "$SRC_INFO" | cut -d'|' -f1)
        SRC_MAC=$(echo "$SRC_INFO" | cut -d'|' -f2)
        SRC_IP=$(echo "$SRC_INFO" | cut -d'|' -f3)
        
        DST_NODE=$(echo "$DST_INFO" | cut -d'|' -f1)
        DST_MAC=$(echo "$DST_INFO" | cut -d'|' -f2)
        DST_IP=$(echo "$DST_INFO" | cut -d'|' -f3)
        
        echo "✓ SOURCE FOUND:"
        echo "  Node: $SRC_NODE"
        [ -n "$SRC_MAC" ] && echo "  MAC: $SRC_MAC"
        [ -n "$SRC_IP" ] && echo "  IP: $SRC_IP"
        echo ""
        
        echo "✓ DESTINATION FOUND:"
        echo "  Node: $DST_NODE"
        [ -n "$DST_MAC" ] && echo "  MAC: $DST_MAC"
        [ -n "$DST_IP" ] && echo "  IP: $DST_IP"
        echo ""
        
        # Now find the path
        echo "Finding path between $SRC_NODE and $DST_NODE..."
        echo ""
        
        find_path_working "$SRC_NODE" "$DST_NODE"
        
    else
        echo "✗ ERROR: Could not find both hosts"
        echo ""
        [ -z "$SRC_INFO" ] && echo "  Source '$src' not found"
        [ -z "$DST_INFO" ] && echo "  Destination '$dst' not found"
        echo ""
        echo "Try these exact commands:"
        echo "  ./$(basename "$0") --debug"
        echo "  ./$(basename "$0") 9a:75:1e:52:59:34 d6:13:51:b3:e3:33"
    fi
}

# Working path finding
find_path_working() {
    local src="$1"
    local dst="$2"
    local TOPOLOGY=$(get_topology)
    
    if [ -z "$TOPOLOGY" ] || [ "$TOPOLOGY" = "null" ]; then
        echo "  No topology data"
        return
    fi
    
    # Get all links
    LINKS=$(echo "$TOPOLOGY" | jq -c '.link[]?' 2>/dev/null)
    
    if [ -z "$LINKS" ]; then
        echo "  No links in topology"
        return
    fi
    
    # Convert to array
    mapfile -t LINK_ARRAY <<< "$LINKS"
    
    # BFS to find path
    declare -A visited
    declare -A parent
    declare -A parent_port
    declare -a queue=("$src")
    visited["$src"]=1
    found=0
    
    while [ ${#queue[@]} -gt 0 ] && [ $found -eq 0 ]; do
        current="${queue[0]}"
        queue=("${queue[@]:1}")
        
        # Find all neighbors
        for link in "${LINK_ARRAY[@]}"; do
            src_node=$(echo "$link" | jq -r '."source"."source-node"')
            src_port=$(echo "$link" | jq -r '."source"."source-tp"')
            dst_node=$(echo "$link" | jq -r '."destination"."dest-node"')
            dst_port=$(echo "$link" | jq -r '."destination"."dest-tp"')
            
            # Forward direction
            if [ "$src_node" = "$current" ] && [ -z "${visited[$dst_node]}" ]; then
                visited["$dst_node"]=1
                parent["$dst_node"]="$src_node"
                parent_port["$dst_node"]="$src_port→$dst_port"
                queue+=("$dst_node")
                
                if [ "$dst_node" = "$dst" ]; then
                    found=1
                    break
                fi
            # Reverse direction
            elif [ "$dst_node" = "$current" ] && [ -z "${visited[$src_node]}" ]; then
                visited["$src_node"]=1
                parent["$src_node"]="$dst_node"
                parent_port["$src_node"]="$dst_port→$src_port"
                queue+=("$src_node")
                
                if [ "$src_node" = "$dst" ]; then
                    found=1
                    break
                fi
            fi
        done
    done
    
    if [ $found -eq 1 ]; then
        echo "  ✓ PATH FOUND:"
        # Reconstruct path
        path=()
        current="$dst"
        
        while [ "$current" != "$src" ]; do
            path=("$current" "${path[@]}")
            current="${parent[$current]}"
        done
        path=("$src" "${path[@]}")
        
        # Display with ports
        for i in $(seq 0 $((${#path[@]} - 2))); do
            from="${path[$i]}"
            to="${path[$((i+1))]}"
            port_info="${parent_port[$to]}"
            
            if [ -n "$port_info" ]; then
                src_port=$(echo "$port_info" | cut -d'|' -f1)
                dst_port=$(echo "$port_info" | cut -d'|' -f2)
                echo "    $from[$src_port] → $to[$dst_port]"
            else
                echo "    $from → $to"
            fi
        done
    else
        echo "  ✗ No path found between $src and $dst"
    fi
}

# Main execution
if [ $# -eq 2 ]; then
    if [ "$1" = "--help" ] || [ "$2" = "--help" ]; then
        echo "Usage:"
        echo "  $0 <source> <destination>  - Trace path between hosts"
        echo "  $0 --debug                 - Show topology debug info"
        echo "  $0 --test <mac_or_ip>      - Test finding a single host"
        echo ""
        echo "Examples:"
        echo "  $0 9a:75:1e:52:59:34 d6:13:51:b3:e3:33"
        echo "  $0 10.0.0.1 10.0.0.2"
        echo "  $0 --debug"
        exit 0
    fi
    
    trace_path_working "$1" "$2"
    
elif [ $# -eq 1 ]; then
    case "$1" in
        "--debug"|"--test-topology")
            show_topology_debug
            ;;
        "--test")
            echo "Usage: $0 --test <mac_or_ip>"
            ;;
        "--help")
            echo "Usage:"
            echo "  $0 <source> <destination>  - Trace path between hosts"
            echo "  $0 --debug                 - Show topology debug info"
            echo "  $0 --test <mac>      - Test finding a single host"
            echo ""
            echo "Examples:"
            echo "  $0 9a:75:1e:52:59:34 d6:13:51:b3:e3:33"
            echo "  $0 10.0.0.1 10.0.0.2"
            echo "  $0 --debug"
            ;;
        *)
            # Test finding a single host
            echo "Testing host lookup: $1"
            echo "======================"
            echo ""
            
            HOST_INFO=$(find_host_simple "$1")
            if [ -n "$HOST_INFO" ]; then
                NODE=$(echo "$HOST_INFO" | cut -d'|' -f1)
                MAC=$(echo "$HOST_INFO" | cut -d'|' -f2)
                IP=$(echo "$HOST_INFO" | cut -d'|' -f3)
                
                echo "✓ HOST FOUND:"
                echo "  Node: $NODE"
                [ -n "$MAC" ] && echo "  MAC: $MAC"
                [ -n "$IP" ] && echo "  IP: $IP"
            else
                echo "✗ Host '$1' not found"
                echo ""
                echo "Running topology debug..."
                show_topology_debug
            fi
            ;;
    esac
else
    echo "Packet Path Tracer for OpenDaylight"
    echo "==================================="
    echo ""
    echo "Usage:"
    echo "  $0 <source> <destination>  - Trace path between hosts"
    echo "  $0 --debug                 - Show topology debug info"
    echo "  $0 --test <mac>      - Test finding a single host"
    echo ""
    echo "Examples:"
    echo "  $0 9a:75:1e:52:59:34 d6:13:51:b3:e3:33"
    echo "  $0 10.0.0.1 10.0.0.2"
    echo "  $0 --debug"
    echo ""
    
    # Quick check
    echo "Quick status:"
    TOPOLOGY=$(get_topology)
    if [ -n "$TOPOLOGY" ] && [ "$TOPOLOGY" != "null" ]; then
        NODES=$(echo "$TOPOLOGY" | jq -r '.node | length' 2>/dev/null || echo "0")
        LINKS=$(echo "$TOPOLOGY" | jq -r '.link | length' 2>/dev/null || echo "0")
        HOSTS=$(echo "$TOPOLOGY" | jq -r '.node[]? | select(."host-tracker-service:addresses"?) | ."node-id"' 2>/dev/null | wc -l)
        echo "  Topology 'flow:1' has:"
        echo "    - $NODES total nodes"
        echo "    - $HOSTS hosts"
        echo "    - $LINKS links"
    else
        echo "  ERROR: Could not load topology 'flow:1'"
    fi
fi


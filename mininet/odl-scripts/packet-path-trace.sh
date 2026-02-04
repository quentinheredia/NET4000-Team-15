#!/bin/bash

CONTROLLER="localhost:8181"
AUTH="admin:admin"

echo "Packet Path Tracer for OpenDaylight"
echo "==================================="
echo ""

# Function to get topology data (cached)
get_topology() {
    if [ -z "$TOPOLOGY_CACHE" ]; then
        TOPOLOGY_CACHE=$(curl -s -u "$AUTH" "http://$CONTROLLER/restconf/operational/network-topology:network-topology/")
    fi
    echo "$TOPOLOGY_CACHE"
}

# Function to detect and display switches
detect_switches() {
    echo "Switch Detection and Analysis"
    echo "============================="
    
    # Get nodes inventory
    NODES_JSON=$(curl -s -u "$AUTH" "http://$CONTROLLER/restconf/operational/opendaylight-inventory:nodes/")
    
    # Method 1: Check OpenDaylight inventory for switches
    echo "1. OpenFlow Switches (from inventory):"
    echo "--------------------------------------"
    
    # Parse JSON for OpenFlow switches
    OF_SWITCHES=$(echo "$NODES_JSON" | jq -r '
        ."nodes"."node"[]? | 
        select(."id"? != null) | 
        "  Switch: \(."id") [State: \(."flow-node-inventory:connected"? // "unknown")]"' 2>/dev/null)
    
    if [ -n "$OF_SWITCHES" ]; then
        echo "$OF_SWITCHES"
        OF_COUNT=$(echo "$OF_SWITCHES" | wc -l)
        echo "  Total OpenFlow switches: $OF_COUNT"
    else
        echo "  No OpenFlow switches found via inventory"
    fi
    
    echo ""
    
    # Method 2: Check topology for all nodes
    echo "2. All Network Nodes (from topology):"
    echo "-------------------------------------"
    
    TOPOLOGY=$(get_topology)
    
    # Get all nodes from topology
    ALL_NODES=$(echo "$TOPOLOGY" | jq -r '.topology[0].node[]? | ."node-id"' 2>/dev/null)
    
    if [ -n "$ALL_NODES" ]; then
        NODE_COUNT=$(echo "$ALL_NODES" | wc -l)
        echo "  Total nodes in topology: $NODE_COUNT"
        echo ""
        
        # Classify each node
        for NODE in $ALL_NODES; do
            # Check if it's a host
            IS_HOST=$(echo "$TOPOLOGY" | jq -r "
                .topology[0].node[]? | 
                select(.\"node-id\" == \"$NODE\") |
                if .\"host-tracker-service:addresses\"? or .\"host-tracker-addresses\"? then \"host\" 
                else \"switch\" end" 2>/dev/null)
            
            # Check if it's OpenFlow switch
            if [[ "$NODE" == openflow:* ]]; then
                NODE_TYPE="OpenFlow switch"
            elif [ "$IS_HOST" == "host" ]; then
                NODE_TYPE="host"
            else
                NODE_TYPE="switch/device"
            fi
            
            # Get MAC addresses for hosts
            if [ "$IS_HOST" == "host" ]; then
                MACS=$(echo "$TOPOLOGY" | jq -r "
                    .topology[0].node[]? | 
                    select(.\"node-id\" == \"$NODE\") |
                    (.\"host-tracker-service:addresses\"[]? // .\"host-tracker-addresses\"[]?) |
                    .mac // empty" 2>/dev/null | tr '\n' ',' | sed 's/,$//')
                echo "  $NODE → Type: $NODE_TYPE [MAC: ${MACS:-none}]"
            else
                echo "  $NODE → Type: $NODE_TYPE"
            fi
        done
    fi
}

# Function to trace path between two hosts
trace_path() {
    local src_mac="$1"
    local dst_mac="$2"
    
    echo "Tracing path from $src_mac to $dst_mac..."
    echo "----------------------------------------"
    
    TOPOLOGY=$(get_topology)
    
    # Check if jq is installed
    if ! command -v jq &> /dev/null; then
        echo "Error: jq is not installed. Please install it first:"
        echo "  Ubuntu/Debian: sudo apt-get install jq"
        echo "  RHEL/CentOS: sudo yum install jq"
        echo "  macOS: brew install jq"
        exit 1
    fi
    
    # Debug: Show raw topology structure
    if [ "${DEBUG:-false}" = "true" ]; then
        echo "Debug: Topology structure:"
        echo "$TOPOLOGY" | jq 'keys' 2>/dev/null
        echo ""
    fi
    
    # Find source and destination nodes using multiple methods
    echo "Looking for hosts..."
    
    # Method 1: Direct query
    SRC_NODE=$(echo "$TOPOLOGY" | jq -r '
        .topology[0].node[]? | 
        select(."host-tracker-service:addresses"?[0]?.mac? == "'"$src_mac"'") | 
        ."node-id"' 2>/dev/null)
    
    DST_NODE=$(echo "$TOPOLOGY" | jq -r '
        .topology[0].node[]? | 
        select(."host-tracker-service:addresses"?[0]?.mac? == "'"$dst_mac"'") | 
        ."node-id"' 2>/dev/null)
    
    # Method 2: Alternative query format (for older/newer ODL versions)
    if [ -z "$SRC_NODE" ] || [ -z "$DST_NODE" ]; then
        SRC_NODE=$(echo "$TOPOLOGY" | jq -r '
            .topology[0].node[]? | 
            ."node-id" as $node |
            ."host-tracker-service:addresses"?[]?.mac? as $mac |
            select($mac == "'"$src_mac"'") | $node' 2>/dev/null)
        
        DST_NODE=$(echo "$TOPOLOGY" | jq -r '
            .topology[0].node[]? | 
            ."node-id" as $node |
            ."host-tracker-service:addresses"?[]?.mac? as $mac |
            select($mac == "'"$dst_mac"'") | $node' 2>/dev/null)
    fi
    
    # Method 3: Even more generic approach
    if [ -z "$SRC_NODE" ] || [ -z "$DST_NODE" ]; then
        SRC_NODE=$(echo "$TOPOLOGY" | jq -r '
            .. | objects | 
            select(.mac? == "'"$src_mac"'") |
            ."node-id" // empty' 2>/dev/null | head -1)
        
        DST_NODE=$(echo "$TOPOLOGY" | jq -r '
            .. | objects | 
            select(.mac? == "'"$dst_mac"'") |
            ."node-id" // empty' 2>/dev/null | head -1)
    fi
    
    if [ -n "$SRC_NODE" ] && [ -n "$DST_NODE" ]; then
        echo "✓ Source: $SRC_NODE (MAC: $src_mac)"
        echo "✓ Destination: $DST_NODE (MAC: $dst_mac)"
        echo ""
        
        # Find links between nodes
        echo "Network Links:"
        echo "--------------"
        
        LINKS=$(echo "$TOPOLOGY" | jq -r '
            .topology[0].link[]? | 
            {
                "src_node": ."source"."source-node",
                "src_port": ."source"."source-tp",
                "dst_node": ."destination"."dest-node",
                "dst_port": ."destination"."dest-tp"
            } |
            "  \(.src_node)[\(.src_port)] → \(.dst_node)[\(.dst_port)]"' 2>/dev/null)
        
        if [ -n "$LINKS" ]; then
            echo "$LINKS"
            
            # Check connectivity
            echo ""
            echo "Connectivity Check:"
            echo "-------------------"
            
            SRC_CONNECTED=$(echo "$LINKS" | grep "$SRC_NODE")
            DST_CONNECTED=$(echo "$LINKS" | grep "$DST_NODE")
            
            if [ -n "$SRC_CONNECTED" ]; then
                echo "✓ Source is connected to network"
            else
                echo "✗ Source not found in network links"
            fi
            
            if [ -n "$DST_CONNECTED" ]; then
                echo "✓ Destination is connected to network"
            else
                echo "✗ Destination not found in network links"
            fi
        else
            echo "No links found in topology"
        fi
    else
        echo "✗ Could not find both hosts in topology"
        echo ""
        
        # Show what hosts ARE available
        echo "Available hosts in network:"
        echo "==========================="
        list_hosts_simple
    fi
}

# Simple function to list hosts
list_hosts_simple() {
    TOPOLOGY=$(get_topology)
    
    # Try multiple methods to extract hosts
    echo "Trying method 1..."
    HOSTS=$(echo "$TOPOLOGY" | jq -r '
        .topology[0].node[]? | 
        select(."host-tracker-service:addresses"? or ."host-tracker-addresses"?) |
        ."node-id" as $node |
        (."host-tracker-service:addresses"[]? // ."host-tracker-addresses"[]?) |
        "  MAC: \(.mac // "unknown") | IP: \(."ip" // "none") | Node: \($node)"' 2>/dev/null)
    
    if [ -n "$HOSTS" ]; then
        echo "$HOSTS"
        return 0
    fi
    
    echo "Trying method 2..."
    HOSTS=$(echo "$TOPOLOGY" | jq -r '
        .. | objects | 
        select(.mac?) |
        "  MAC: \(.mac) | Node: \(."node-id" // "unknown") | IP: \(."ip" // "none")"' 2>/dev/null | sort -u)
    
    if [ -n "$HOSTS" ]; then
        echo "$HOSTS"
        return 0
    fi
    
    echo "Trying method 3 (raw dump)..."
    # Last resort: show all MAC addresses found
    MACS=$(echo "$TOPOLOGY" | jq -r '.. | .mac? // empty' 2>/dev/null | sort -u)
    if [ -n "$MACS" ]; then
        echo "$MACS" | while read MAC; do
            echo "  MAC: $MAC"
        done
    else
        echo "No hosts/MAC addresses found in topology"
        echo ""
        echo "Topology structure:"
        echo "$TOPOLOGY" | jq '.' 2>/dev/null | head -50
    fi
}

# Function to list all hosts (detailed)
list_hosts() {
    echo "Available hosts in network:"
    echo "==========================="
    
    TOPOLOGY=$(get_topology)
    
    # First, try to get the topology type
    TOPOLOGY_TYPE=$(echo "$TOPOLOGY" | jq -r '.topology[0]."topology-id" // "default"' 2>/dev/null)
    echo "Topology ID: $TOPOLOGY_TYPE"
    echo ""
    
    # Get all nodes that have MAC addresses
    HOSTS=$(echo "$TOPOLOGY" | jq -r '
        .topology[0].node[]? | 
        ."node-id" as $node |
        (."host-tracker-service:addresses"? // ."host-tracker-addresses"? // []) as $addrs |
        $addrs[]? |
        "  MAC: \(.mac // "unknown") | IP: \(."ip" // "none") | VLAN: \(."vlan" // "none") | Node: \($node)"' 2>/dev/null)
    
    if [ -n "$HOSTS" ]; then
        echo "$HOSTS"
        HOST_COUNT=$(echo "$HOSTS" | wc -l 2>/dev/null || echo "0")
        echo ""
        echo "Total hosts found: $HOST_COUNT"
    else
        echo "No hosts found using standard method."
        echo ""
        echo "Debug: Looking for any MAC addresses in topology..."
        
        # Alternative: find any MAC addresses anywhere in the JSON
        ALL_MACS=$(echo "$TOPOLOGY" | jq -r '
            .. | objects | 
            select(.mac?) |
            "  MAC: \(.mac) | Type: \(."address-type" // "unknown") | Node: \(."node-id" // "unknown")"' 2>/dev/null | sort -u)
        
        if [ -n "$ALL_MACS" ]; then
            echo "Found MAC addresses:"
            echo "$ALL_MACS"
        else
            echo "Could not find any MAC addresses in topology"
            echo ""
            echo "Topology nodes (raw):"
            echo "$TOPOLOGY" | jq -r '.topology[0].node[]? | ."node-id"' 2>/dev/null
        fi
    fi
}

# Function to show flows
show_flows_for_path() {
    echo ""
    echo "Flow entries:"
    echo "============="
    
    # Get all switch nodes
    SWITCHES=$(curl -s -u "$AUTH" "http://$CONTROLLER/restconf/operational/opendaylight-inventory:nodes/" | 
               jq -r '.nodes.node[]? | select(."id"? | startswith("openflow:")) | ."id"' 2>/dev/null)
    
    if [ -z "$SWITCHES" ]; then
        SWITCHES=$(curl -s -u "$AUTH" "http://$CONTROLLER/restconf/operational/opendaylight-inventory:nodes/" | 
                   grep -o '"id":"openflow:[0-9]*"' | sed 's/"id":"//g' | sed 's/"//g' | sort -u)
    fi
    
    if [ -z "$SWITCHES" ]; then
        echo "No OpenFlow switches found"
        return
    fi
    
    SWITCH_COUNT=$(echo "$SWITCHES" | wc -w)
    echo "Found $SWITCH_COUNT OpenFlow switches"
    echo ""
    
    for SWITCH in $SWITCHES; do
        echo "Switch: $SWITCH"
        echo "--------------"
        
        # Get basic switch info
        SWITCH_INFO=$(curl -s -u "$AUTH" "http://$CONTROLLER/restconf/operational/opendaylight-inventory:nodes/node/$SWITCH/" | 
                      jq -r '."node"[]? | 
                      "  Manufacturer: \(."flow-node-inventory:manufacturer"? // "unknown") | " +
                      "Hardware: \(."flow-node-inventory:hardware"? // "unknown") | " +
                      "Software: \(."flow-node-inventory:software"? // "unknown") | " +
                      "Serial: \(."flow-node-inventory:serial-number"? // "unknown")"' 2>/dev/null)
        
        if [ -n "$SWITCH_INFO" ]; then
            echo "$SWITCH_INFO"
        fi
        
        # Try to get flow table 0
        FLOWS_JSON=$(curl -s -u "$AUTH" "http://$CONTROLLER/restconf/operational/opendaylight-inventory:nodes/node/$SWITCH/flow-node-inventory:table/0/" 2>/dev/null)
        
        if [ -n "$FLOWS_JSON" ] && [ "$FLOWS_JSON" != "{}" ]; then
            FLOW_COUNT=$(echo "$FLOWS_JSON" | grep -c '"id"' || echo "0")
            echo "  Flows in table 0: $FLOW_COUNT"
            
            if [ "$FLOW_COUNT" -gt 0 ]; then
                # Show simple flow info
                echo "$FLOWS_JSON" | jq -r '."flow-node-inventory:table"[0]."flow"[]? | 
                    "    [\(."id")] Priority: \(."priority" // "0")"' 2>/dev/null | head -5
                
                if [ "$FLOW_COUNT" -gt 5 ]; then
                    echo "    ... and $((FLOW_COUNT - 5)) more"
                fi
            fi
        else
            echo "  No flows or unable to read flow table"
        fi
        
        echo ""
    done
}

# Function to show network summary
show_summary() {
    echo "Network Summary"
    echo "==============="
    
    TOPOLOGY=$(get_topology)
    
    # Count total nodes
    TOTAL_NODES=$(echo "$TOPOLOGY" | jq -r '.topology[0].node[]? | ."node-id"' 2>/dev/null | wc -l)
    echo "Total nodes in topology: $TOTAL_NODES"
    
    # Count links
    TOTAL_LINKS=$(echo "$TOPOLOGY" | jq -r '.topology[0].link[]? | ."link-id"' 2>/dev/null | wc -l)
    echo "Total links in topology: $TOTAL_LINKS"
    
    # Count OpenFlow switches
    OF_SWITCHES=$(curl -s -u "$AUTH" "http://$CONTROLLER/restconf/operational/opendaylight-inventory:nodes/" | 
                  jq -r '.nodes.node[]? | select(."id"? | startswith("openflow:")) | ."id"' 2>/dev/null | wc -l)
    echo "OpenFlow switches: $OF_SWITCHES"
    
    # Count hosts (MAC addresses)
    HOST_COUNT=$(echo "$TOPOLOGY" | jq -r '.. | .mac? // empty' 2>/dev/null | sort -u | wc -l)
    echo "Unique MAC addresses: $HOST_COUNT"
    
    echo ""
    echo "Quick host list (first 10):"
    echo "---------------------------"
    echo "$TOPOLOGY" | jq -r '.. | .mac? // empty' 2>/dev/null | sort -u | head -10 | while read MAC; do
        echo "  $MAC"
    done
}

# Main execution
if [ $# -eq 2 ]; then
    trace_path "$1" "$2"
    show_flows_for_path
elif [ $# -eq 1 ]; then
    case "$1" in
        "--list-hosts")
            list_hosts
            ;;
        "--detect-switches")
            detect_switches
            ;;
        "--summary")
            show_summary
            ;;
        "--all")
            echo "=== SWITCHES ==="
            detect_switches
            echo ""
            echo "=== HOSTS ==="
            list_hosts
            ;;
        "--debug")
            DEBUG=true
            echo "Debug mode enabled"
            TOPOLOGY=$(get_topology)
            echo "Topology sample:"
            echo "$TOPOLOGY" | jq '.' 2>/dev/null | head -100
            ;;
        *)
            echo "Unknown option: $1"
            echo ""
            echo "Usage:"
            echo "  $0 <source_mac> <destination_mac>"
            echo "  $0 --list-hosts"
            echo "  $0 --detect-switches"
            echo "  $0 --summary"
            echo "  $0 --all"
            echo "  $0 --debug"
            ;;
    esac
else
    echo "Usage:"
    echo "  $0 <source_mac> <destination_mac>"
    echo "  $0 --list-hosts"
    echo "  $0 --detect-switches"
    echo "  $0 --summary"
    echo "  $0 --all"
    echo "  $0 --debug"
    echo ""
    echo "Examples:"
    echo "  $0 00:00:00:00:00:01 00:00:00:00:00:02"
    echo "  $0 --list-hosts"
    echo "  $0 --summary"
    echo ""
    show_summary
fi


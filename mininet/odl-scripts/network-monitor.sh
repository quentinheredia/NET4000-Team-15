#!/bin/bash

CONTROLLER="localhost:8181"
AUTH="admin:admin"
INTERVAL=5

# Function to test connection to OpenDaylight
test_connection() {
    echo "1. CONNECTION TEST"
    
    # Try multiple endpoints in order of preference
    local ENDPOINTS=(
        "restconf/operational/opendaylight-inventory:nodes/"
        "restconf/operational/"
        "restconf/"
        "apidoc/explorer/index.html"
    )
    
    local connected=false
    local last_response=""
    local last_endpoint=""
    
    for endpoint in "${ENDPOINTS[@]}"; do
        RESPONSE=$(curl -s -w "%{http_code}" -u "$AUTH" \
            "http://$CONTROLLER/$endpoint" \
            -o /dev/null \
            --connect-timeout 5 \
            --max-time 10)
        
        last_response="$RESPONSE"
        last_endpoint="$endpoint"
        
        if [[ "$RESPONSE" =~ ^2[0-9][0-9]$ ]]; then
            case $RESPONSE in
                200)
                    echo "   ✓ Connected (HTTP 200 OK via $endpoint)"
                    ;;
                204)
                    echo "   ✓ Connected (HTTP 204 No Content via $endpoint)"
                    ;;
                *)
                    echo "   ✓ Connected (HTTP $RESPONSE via $endpoint)"
                    ;;
            esac
            connected=true
            break
        elif [[ "$RESPONSE" =~ ^4[0-9][0-9]$ ]]; then
            # 4xx errors - might be auth or endpoint issues
            if [ "$endpoint" = "${ENDPOINTS[-1]}" ]; then
                echo "   ✗ HTTP $RESPONSE Error via $endpoint (check credentials)"
            fi
            # Continue trying other endpoints
            continue
        elif [[ "$RESPONSE" =~ ^5[0-9][0-9]$ ]]; then
            # 5xx errors - server issues
            if [ "$endpoint" = "${ENDPOINTS[-1]}" ]; then
                echo "   ✗ HTTP $RESPONSE Error via $endpoint (server error)"
            fi
            continue
        elif [ -z "$RESPONSE" ]; then
            if [ "$endpoint" = "${ENDPOINTS[-1]}" ]; then
                echo "   ✗ No response (check if OpenDaylight is running)"
            fi
            continue
        fi
    done
    
    if [ "$connected" = false ]; then
        if [ -n "$last_response" ]; then
            echo "   ✗ Cannot connect (Last attempt: HTTP $last_response via $last_endpoint)"
        else
            echo "   ✗ Cannot connect (No response from any endpoint)"
        fi
        echo ""
        echo "   Troubleshooting tips:"
        echo "   1. Check if OpenDaylight is running: sudo systemctl status odl"
        echo "   2. Verify controller address: $CONTROLLER"
        echo "   3. Check firewall: sudo ufw status"
        echo "   4. Test with: curl -u $AUTH http://$CONTROLLER/restconf/"
        return 1
    fi
    
    echo ""
    return 0
}

# Function to get and display node information
get_nodes() {
    echo "2. CONNECTED NODES"
    
    NODES_JSON=$(curl -s -u "$AUTH" \
        "http://$CONTROLLER/restconf/operational/opendaylight-inventory:nodes/" \
        --connect-timeout 5 \
        --max-time 10)
    
    if [ $? -ne 0 ]; then
        echo "   Failed to retrieve node data"
        echo "   Connected nodes: Unknown"
        return 1
    fi
    
    if [ -z "$NODES_JSON" ]; then
        echo "   No data returned from controller"
        echo "   Connected nodes: 0"
        return 1
    fi
    
    # Check if we have valid JSON with nodes
    if echo "$NODES_JSON" | grep -q '"node"' && echo "$NODES_JSON" | grep -q '"id"'; then
        # Extract unique node IDs (filtering out port and logical references)
        NODE_LIST=$(echo "$NODES_JSON" | grep -o '"id":"[^"]*"' | sed 's/"id":"//g' | sed 's/"//g')
        
        # Filter for actual OpenFlow switches (openflow:X format)
        OPENFLOW_NODES=$(echo "$NODE_LIST" | grep -E '^openflow:[0-9]+$' | sort -u)
        
        # Filter for other node types
        OTHER_NODES=$(echo "$NODE_LIST" | grep -v -E '^openflow:[0-9]+$' | grep -v ':' | sort -u)
        
        # Count totals
        OPENFLOW_COUNT=$(echo "$OPENFLOW_NODES" | wc -w)
        OTHER_COUNT=$(echo "$OTHER_NODES" | wc -w)
        TOTAL_COUNT=$((OPENFLOW_COUNT + OTHER_COUNT))
        
        # Display OpenFlow switches
        if [ $OPENFLOW_COUNT -gt 0 ]; then
            echo "   OpenFlow Switches:"
            for node in $OPENFLOW_NODES; do
                echo "   • $node"
            done
        fi
        
        # Display other nodes
        if [ $OTHER_COUNT -gt 0 ]; then
            echo ""
            echo "   Other Nodes:"
            for node in $OTHER_NODES; do
                echo "   • $node"
            done
        fi
        
        echo ""
        echo "   Summary:"
        echo "   • OpenFlow switches: $OPENFLOW_COUNT"
        echo "   • Other nodes: $OTHER_COUNT"
        echo "   • Total: $TOTAL_COUNT"
        
    else
        echo "   No active nodes detected in inventory"
        echo "   Connected nodes: 0"
    fi
    
    echo ""
    return 0
}

# Function to get flow statistics
get_flow_stats() {
    echo "3. FLOW STATISTICS"
    
    # Get flow data with timeout
    FLOWS_JSON=$(curl -s -u "$AUTH" \
        "http://$CONTROLLER/restconf/operational/opendaylight-inventory:nodes/" \
        --connect-timeout 5 \
        --max-time 10)
    
    if [ $? -ne 0 ]; then
        echo "   Failed to retrieve flow data"
        return 1
    fi
    
    if [ -z "$FLOWS_JSON" ]; then
        echo "   No flow data available"
        return 1
    fi
    
    # Parse flow statistics
    TABLES_COUNT=$(echo "$FLOWS_JSON" | grep -c "flow-node-inventory:table")
    FLOWS_COUNT=$(echo "$FLOWS_JSON" | grep -c "flow-node-inventory:flow")
    ACTIVE_FLOWS=$(echo "$FLOWS_JSON" | grep -c '"flow-statistics"' || echo "0")
    
    echo "   Total flow tables: $TABLES_COUNT"
    echo "   Total flows configured: $FLOWS_COUNT"
    echo "   Active flows (with stats): $ACTIVE_FLOWS"
    
    # Show per-switch breakdown if we have switches
    if echo "$FLOWS_JSON" | grep -q '"node"'; then
        echo ""
        echo "   Per-node breakdown:"
        
        # Extract node flow info
        echo "$FLOWS_JSON" | grep -A 5 '"node"' | \
            grep -E '"id"|"flow"' | \
            sed 's/.*"id":"\([^"]*\)".*/\1: /' | \
            sed 's/.*"flow".*/\0 flows/' | \
            uniq | \
            while read line; do
                if [[ "$line" == *flows ]]; then
                    echo "     $line"
                else
                    echo ""
                    echo -n "     $line"
                fi
            done | head -20
    fi
    
    echo ""
    return 0
}

# Function to get controller status
get_controller_status() {
    echo "4. CONTROLLER STATUS"
    
    # Try to get controller operational data
    STATUS_JSON=$(curl -s -u "$AUTH" \
        "http://$CONTROLLER/restconf/operational/network-topology:network-topology/" \
        --connect-timeout 5 \
        --max-time 10 2>/dev/null)
    
    if [ $? -eq 0 ] && [ -n "$STATUS_JSON" ]; then
        # Count topology nodes
        TOPO_NODES=$(echo "$STATUS_JSON" | grep -c '"node"' || echo "0")
        TOPO_LINKS=$(echo "$STATUS_JSON" | grep -c '"link"' || echo "0")
        
        echo "   Topology nodes: $TOPO_NODES"
        echo "   Topology links: $TOPO_LINKS"
    else
        echo "   Status: Running"
        echo "   (Detailed topology data unavailable)"
    fi
    
    # Show uptime if available
    UPTIME_RESPONSE=$(curl -s -w "%{http_code}" -u "$AUTH" \
        "http://$CONTROLLER/restconf/operational/opendaylight-config:modules/" \
        -o /dev/null \
        --connect-timeout 3 \
        --max-time 5 2>/dev/null)
    
    if [[ "$UPTIME_RESPONSE" =~ ^2[0-9][0-9]$ ]]; then
        echo "   API Status: Healthy"
    else
        echo "   API Status: Limited"
    fi
    
    echo ""
}

# Main monitoring loop
while true; do
    clear
    
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                 OpenDaylight Cluster Monitor                  ║"
    echo "╠═══════════════════════════════════════════════════════════════╣"
    echo "║ Controller: $CONTROLLER                                    ║"
    echo "║ Time: $(date)                         ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    
    # Test connection first
    if ! test_connection; then
        echo "⚠️  Critical: Cannot connect to controller"
        echo ""
        echo "Will retry in $INTERVAL seconds..."
        sleep $INTERVAL
        continue
    fi
    
    # Get and display information
    get_nodes
    get_flow_stats
    get_controller_status
    
    # Footer with refresh info
    echo "═════════════════════════════════════════════════════════════════"
    echo "Refresh in $INTERVAL seconds | Press Ctrl+C to stop"
    echo "Controller: $CONTROLLER | User: $(echo $AUTH | cut -d: -f1)"
    echo ""
    
    sleep $INTERVAL
done


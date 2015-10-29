#!/bin/sh

set -e

IPTABLES=iptables

help() {
    echo "Usage: ${0} -h " >&2
    echo "       ${0} start <remote_host[:port]>" >&2
    echo "       ${0} start <counter_name> [-l <local_host[:port]>] [-r <remote_host[:port]>]" >&2
    echo "       ${0} show [<counter_name>]" >&2
    echo "       ${0} stop [<counter_name>]" >&2
    echo " " >&2
    echo "'... start remote:port' is equivalent to '... start remote:port -r remote:port'" >&2
    echo "Omitting <counter_name> selects all counters in show, stop" >&2
}

if [ -z "$1" ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    help
    exit 1
fi

cmd="$1"
shift

start_counter() {
    CHAIN=$(echo $CHAIN | head -c 27)
    $IPTABLES -w -N ${CHAIN}i
    $IPTABLES -w -N ${CHAIN}o

    if [ -z "$R_HOST" ]; then
        R_HOST="0.0.0.0/0"
    fi
    if [ -z "$L_HOST" ]; then
        L_HOST="0.0.0.0/0"
    fi
    if [ -z "$R_PORT" ]; then
        R_PORT="0:65535"
    fi
    if [ -z "$L_PORT" ]; then
        L_PORT="0:65535"
    fi

    $IPTABLES -w -A ${CHAIN}i -p tcp \
            -s "$R_HOST" --sport "$R_PORT" \
            -d "$L_HOST" --dport "$L_PORT" \
            -j RETURN
    $IPTABLES -w -I INPUT 1 -j ${CHAIN}i

    $IPTABLES -w -A ${CHAIN}o -p tcp \
            -d "$R_HOST" --dport "$R_PORT" \
            -s "$L_HOST" --sport "$L_PORT" \
            -j RETURN
    $IPTABLES -w -I OUTPUT 1 -j ${CHAIN}o
}

stop_counter() {
    CHAIN=$(echo $CHAIN | head -c 27)
    $IPTABLES -w -D INPUT -j ${CHAIN}i > /dev/null 2>&1 || true
    $IPTABLES -w -D OUTPUT -j ${CHAIN}o > /dev/null 2>&1 || true
    $IPTABLES -w -F ${CHAIN}i > /dev/null 2>&1 || true
    $IPTABLES -w -X ${CHAIN}i > /dev/null 2>&1 || true
    $IPTABLES -w -F ${CHAIN}o > /dev/null 2>&1 || true
    $IPTABLES -w -X ${CHAIN}o > /dev/null 2>&1 || true
}

show_counter() {
    CHAIN=$(echo $CHAIN | head -c 27)
    if $IPTABLES -w -xvnL ${CHAIN}i 2> /dev/null | grep -q RETURN; then
        bytes_in=$($IPTABLES -w -xvnL ${CHAIN}i | grep RETURN | \
            sed -e 's/^ *[0-9]* *\([0-9]*\) .*$/\1 + \\/; $ a 0' | bc)
    else
        bytes_in=0
    fi

    if $IPTABLES -w -xvnL ${CHAIN}o 2> /dev/null | grep -q RETURN; then
        bytes_out=$($IPTABLES -w -xvnL ${CHAIN}o | grep RETURN | \
            sed -e 's/^ *[0-9]* *\([0-9]*\) .*$/\1 + \\/; $ a 0' | bc)
    else
        bytes_out=0
    fi
    bytes_total=$(echo $bytes_in + $bytes_out | bc)
    echo ${NAME} in ${bytes_in} out ${bytes_out} total ${bytes_total}
}

case ${cmd} in
    start)
        NAME="$1"
        shift;
        if [ -z "$NAME" ]; then
            echo "'start' command requires a name" >&2
            echo " " >&2
            help
            exit 3
        fi

        export R_HOST=
        export R_PORT=
        export L_HOST=
        export L_PORT=

        if [ -z "$1" ]; then
            set -- -r ${NAME}
        fi

        while getopts l:r: opt; do
            case $opt in
                r)
                    IFS=':'
                    i=0
                    for v in ${OPTARG}; do
                        case $i in
                            0)
                                export R_HOST="$v"
                                ;;
                            1)
                                export R_PORT="$v"
                                ;;
                            *)
                                break
                                ;;
                        esac
                        i=$[i+1]
                    done
                    unset IFS
                    shift 2
                    ;;
                l)
                    IFS=':'
                    i=0
                    for v in ${OPTARG}; do
                        case $i in
                            0)
                                export L_HOST="$v"
                                ;;
                            1)
                                export L_PORT="$v"
                                ;;
                            *)
                                break
                                ;;
                        esac
                        i=$[i+1]
                    done
                    shift 2
                    ;;
                *)
                    ;;
            esac
        done

        if [ -n "$1" ]; then
            echo "Unexpected argument: ${1}" >&2
            echo " " >&2
            help
            exit 4
        fi

        export CHAIN=count_${NAME}
        start_counter
        ;;

    show)
        if [ -z "$1" ]; then
            NAMES=$(iptables -nL | grep "Chain count_" | \
                sed -e 's/^Chain count_\([^ ]*\)[io] .*$/\1/' | uniq)
        else
            NAMES=$1
        fi
        for NAME in ${NAMES}; do
            CHAIN=count_${NAME}
            show_counter
        done
        ;;

    stop)
        if [ -z "$1" ]; then
            NAMES=$(iptables -nL | grep "Chain count_" | \
                sed -e 's/^Chain count_\([^ ]*\)[io] .*$/\1/' | uniq)
        else
            NAMES=$1
        fi
        for NAME in ${NAMES}; do
            CHAIN=count_${NAME}
            show_counter
            stop_counter
        done
        ;;

    *)
        help
        exit 5
esac

import socket
import struct
import time

# --- Configuration ---
HOST = "127.0.0.1"
PORT = 5001
SIG_FMT = '<I i f I'

# --- The Signal ---
# (Contract ID, Target Position, Confidence, Timestamp)
# Note: You MUST change '265598' to the actual conId of SPY after your system boots.
conId      = 265598 
targetPos  = 10     
confidence = 0.95   
timestamp  = int(time.time())

# Pack the binary struct
payload = struct.pack(SIG_FMT, conId, targetPos, confidence, timestamp)

# Send the UDP Packet
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.sendto(payload, (HOST, PORT))
sock.close()

print(f"Signal fired to Port {PORT}: [ID:{conId} | Target:{targetPos} | Alpha:{confidence:.2f}]")
#!/usr/bin/python3

# Repeater Firmware: A8.05.07.001
# Konvertierung nach Wave: $ sox -r 8000 -t raw -e u-law -c 1 HytBridge.TS1.raw out.wav

import socket
import _thread
import time
import signal
import sys
import audioop
import wave

# IP-Adresse vom Repeater:
#LOCAL_IP = "127.0.0.1"
#RPT_IP = "127.0.0.1"
LOCAL_IP = "192.168.0.115"
RPT_IP = "192.168.0.201"

# UDP-Ports für die Steuerung:
RCP_PORT_TS1 = 30009
RCP_PORT_TS2 = 30010

# UDP-Ports für die Audio-Daten:
RTP_PORT_TS1 = 30012
RTP_PORT_TS2 = 30014

# Bei STRG+C beenden:
def signal_handler(signal, frame):
  print("Abort!")
  sys.exit(0)

def decodeCallType(ct):
  CallTypeList = ["Pvt", "Grp", "All"]
  if ct >= 0 and ct < len(CallTypeList):
    return CallTypeList[ct]
  return "invalid"

def isQSOData(data):
  return len(data) == 38 and data[0] == 0x32 and data[1] == 0x42 and data[2] == 0x00 and data[3] == 0x20

def printQSOData(threadName, data):
  RptId = int("%02X%02X%02X" % (data[9], data[10], data[11]), 16)
  CT = decodeCallType(data[26])
  DstId = int("%02X%02X%02X" % (data[30], data[29], data[28]), 16)
  SrcId = int("%02X%02X%02X" % (data[34], data[33], data[32]), 16)
  print(threadName, ":", CT, "call from", SrcId, "to", DstId, "via", RptId)

# Klasse, die sich um das Audio (RCP+RTP) für einen Timeslot kümmert.
class AudioSlot:
  def __init__(self, name, RptIP, RCP_Port, RTP_Port):
    # Portnummern merken:
    self.name = name
    self.RptIP = RptIP
    self.RCP_Port = RCP_Port
    self.RTP_Port = RTP_Port

    # Constants:
    self.WakeCallPacket = bytes.fromhex('324200050000')
    self.IdleKeepAlivePacket = bytes.fromhex('324200020000')
    self.PCMSAMPLERATE = 8000
    self.RTP_DATA_SIZE = 160

    # Tx-Buffer:
    self.TxBufferULaw = bytearray()
    self.PTT = False
    self.RTP_Seq = int(time.time() * self.PCMSAMPLERATE)
    self.RTP_Timestamp = self.RTP_Seq

    self.RCP_Seq = self.RTP_Seq
    self.CallType = 0 # 0: Private 1: Group 2: AllCall
    self.DstId = 0

    # Sockets anlegen:
    self.RCP_Sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    self.RTP_Sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Sockets an Ports binden:
    self.RCP_Sock.bind((LOCAL_IP, RCP_Port))
    self.RTP_Sock.bind((LOCAL_IP, RTP_Port))
#    try:
    _thread.start_new_thread(self.RCP_Rx_Thread, (name,))
    _thread.start_new_thread(self.RTP_Rx_Thread, (name,))
    _thread.start_new_thread(self.TxIdleMsgThread, (name,))
    _thread.start_new_thread(self.TxAudioThread, (name,))
#    except:
#      print("ERROR: Unable to start threads!", name)

  def getNextRCPSeq(self):
    self.RCP_Seq = (self.RCP_Seq + 1) & 0xFF
    return self.RCP_Seq

  def sendACK(self, seq):
    AckPacket = bytearray.fromhex('324200010100')
    AckPacket[5] = seq;
    self.RCP_Sock.sendto(AckPacket, (self.RptIP, self.RCP_Port))

  def sendCallSetup(self, CallType, DstId):
    packet = bytearray.fromhex('3242000000010241080500017c0900005e03')
    packet[5] = self.getNextRCPSeq()
    packet[11] = CallType
    packet[12] = DstId & 0xFF
    packet[13] = (DstId >> 8) & 0xFF
    packet[14] = (DstId >> 16) & 0xFF
    self.RCP_Sock.sendto(packet, (self.RptIP, self.RCP_Port))

  def sendPTT(self, onoff):
    packet = bytearray.fromhex('32420000000002410002000300ec03')
    packet[5] = self.getNextRCPSeq()
    if onoff:
      packet[12:14] = bytes.fromhex('01eb')
    self.RCP_Sock.sendto(packet, (self.RptIP, self.RCP_Port))

  def sendAudioFrame(self):
    bytesToSend = self.TxBufferULaw[0:self.RTP_DATA_SIZE]
    self.TxBufferULaw = self.TxBufferULaw[self.RTP_DATA_SIZE:]
    while len(bytesToSend) < self.RTP_DATA_SIZE:
      bytesToSend.append(0xFF)
    rtp = bytearray.fromhex('90000000000000000000000000150003000000000000000000000000') + bytesToSend
    self.RTP_Seq = (self.RTP_Seq + 1) & 0xFFFF
    self.RTP_Timestamp = (self.RTP_Timestamp + self.RTP_DATA_SIZE) & 0xFFFFFFFF
    rtp[2] = (self.RTP_Seq >> 8) & 0xFF
    rtp[3] = self.RTP_Seq & 0xFF
    rtp[4] = (self.RTP_Timestamp >> 24) & 0xFF
    rtp[5] = (self.RTP_Timestamp >> 16) & 0xFF
    rtp[6] = (self.RTP_Timestamp >> 8) & 0xFF
    rtp[7] = self.RTP_Timestamp & 0xFF
    self.RTP_Sock.sendto(rtp, (self.RptIP, self.RTP_Port))

  def RCP_Rx_Thread(self, threadName):
    #print(threadName, "RCP_Rx_Thread started")
    while True:
      data, addr = self.RCP_Sock.recvfrom(1024)
      #print(threadName, "RCP_Rx_Thread: received message:", data)
      if isQSOData(data):
        self.sendACK(data[5])
        printQSOData(threadName, data)

  def RTP_Rx_Thread(self, threadName):
    #print(threadName, "RTP_Rx_Thread started")
    wavefile = wave.open("HytAudioBridge." + threadName + ".wav", 'wb')
    wavefile.setparams((1, 2, self.PCMSAMPLERATE, 0, 'NONE', 'not compressed'))
    while True:
      data, addr = self.RTP_Sock.recvfrom(1024)
      #print(threadName, "RTP_Rx_Thread: received message:", data)
      if data[0:2] == bytes.fromhex('9000'):
        wavefile.writeframes(audioop.ulaw2lin(data[28:], 2))

  def TxIdleMsgThread(self, threadName):
    #print(threadName, "TxIdleMsgThread started")
    self.RCP_Sock.sendto(self.WakeCallPacket, (self.RptIP, self.RCP_Port))
    self.RTP_Sock.sendto(self.WakeCallPacket, (self.RptIP, self.RTP_Port))
    while True:
      self.RCP_Sock.sendto(self.IdleKeepAlivePacket, (self.RptIP, self.RCP_Port))
      self.RTP_Sock.sendto(self.IdleKeepAlivePacket, (self.RptIP, self.RTP_Port))
      time.sleep(2)

  def TxAudioThread(self, threadName):
    #print(threadName, "TxAudioThread started")
    while True:
      if len(self.TxBufferULaw) > 0:
        if not self.PTT:
          self.sendCallSetup(self.CallType, self.DstId)
          time.sleep(0.1)
          self.sendPTT(True)
          self.PTT = True
      else:
        if self.PTT:
          self.sendPTT(False)
          self.sendPTT(False)
          self.PTT = False
      self.sendAudioFrame()
      time.sleep(self.RTP_DATA_SIZE * 0.99 / self.PCMSAMPLERATE) # rough guess, not very accurate!

  def playFile(self, wavefilename, CallType, DstId):
    self.CallType = CallType
    self.DstId = DstId
    wavefile = wave.open(wavefilename, 'rb')
    # TODO: Check format and convert if necessary
    self.TxBufferULaw += audioop.lin2ulaw(wavefile.readframes(3 * 60 * self.PCMSAMPLERATE), 2) # load max 3min
    wavefile.close()

print("HytAudioBridge 0.01")
signal.signal(signal.SIGINT, signal_handler)

AudioSlot1 = AudioSlot("TS1", RPT_IP, RCP_PORT_TS1, RTP_PORT_TS1)
AudioSlot2 = AudioSlot("TS2", RPT_IP, RCP_PORT_TS2, RTP_PORT_TS2)

print("Waiting...")
time.sleep(5)
print("Sending...")
AudioSlot1.playFile("testmsg.wav", 1, 2428)
#AudioSlot1.playFile("testmsg.wav", 1, 2429)
#AudioSlot1.playFile("testmsg.wav", 0, 2623305)
time.sleep(10)
#print("Sending...")
AudioSlot1.playFile("testmsg.wav", 1, 2428)
#AudioSlot1.playFile("testmsg.wav", 0, 2623305)
time.sleep(45)

print("Exit!")
sys.exit(0)

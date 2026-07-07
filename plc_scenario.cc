#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/mobility-module.h"
#include "ns3/internet-module.h"
#include "ns3/wifi-module.h"
#include "ns3/applications-module.h"
#include "ns3/traffic-control-module.h"
#include <fstream>
#include <string>
#include <vector>
using namespace ns3;

// ---------- tags (timestamp + sender id) ----------
struct StampTag : public Tag {
  Time t; uint32_t sender;
  static TypeId GetTypeId () {
    static TypeId tid = TypeId("StampTag").SetParent<Tag>().AddConstructor<StampTag>();
    return tid;
  }
  TypeId GetInstanceTypeId () const override { return GetTypeId(); }
  uint32_t GetSerializedSize () const override { return 16; }
  void Serialize (TagBuffer i) const override { i.WriteU64(t.GetNanoSeconds()); i.WriteU32(sender); i.WriteU32(0); }
  void Deserialize (TagBuffer i) override { t = NanoSeconds(i.ReadU64()); sender = i.ReadU32(); (void)i.ReadU32(); }
  void Print (std::ostream& os) const override { os << t.GetSeconds() << "," << sender; }
};

// ---------- global logs ----------
static std::ofstream g_txLog, g_rxLog, g_radioLog, g_queueLog;

// ---------- queue accounting ----------
static std::vector<uint32_t> g_qLen;   // live queue length (pkts)
static std::vector<uint32_t> g_qDrops; // cumulative drops (pkts)
static uint32_t g_qMaxPackets = 100;   // desired limit in packets

static void OnEnqueue (uint32_t i, Ptr<const QueueDiscItem>) {
  if (i < g_qLen.size()) g_qLen[i] += 1;
  g_queueLog << Simulator::Now().GetSeconds() << "," << i << "," << g_qLen[i] << "," << g_qDrops[i] << "\n";
}
static void OnDequeue (uint32_t i, Ptr<const QueueDiscItem>) {
  if (i < g_qLen.size() && g_qLen[i] > 0) g_qLen[i] -= 1;
  g_queueLog << Simulator::Now().GetSeconds() << "," << i << "," << g_qLen[i] << "," << g_qDrops[i] << "\n";
}
static void OnDrop (uint32_t i, Ptr<const QueueDiscItem>) {
  if (i < g_qDrops.size()) g_qDrops[i] += 1;
  g_queueLog << Simulator::Now().GetSeconds() << "," << i << "," << g_qLen[i] << "," << g_qDrops[i] << "\n";
}

// ---------- Wi-Fi monitor sniffer (ns-3.45 signature) ----------
static void MonitorSnifferRx (Ptr<const Packet>, uint16_t freqMhz,
                              WifiTxVector, MpduInfo, SignalNoiseDbm sn, uint16_t /*staId*/)
{
  g_radioLog << Simulator::Now().GetSeconds() << "," << freqMhz
             << "," << sn.signal << "," << sn.noise << "\n";
}

// ---------- tiny UDP apps ----------
class UdpSender : public Application {
public:
  void Setup (Ipv4Address dst, uint16_t port, uint32_t pps, uint32_t bytes) {
    m_dst = InetSocketAddress (dst, port); m_pps = pps; m_bytes = bytes;
  }
private:
  void StartApplication () override {
    m_sock = Socket::CreateSocket (GetNode (), UdpSocketFactory::GetTypeId ());
    m_sock->Connect (m_dst);
    m_interval = Seconds (1.0 / std::max (1u, m_pps));
    ScheduleTx ();
  }
  void ScheduleTx () { Simulator::Schedule (m_interval, &UdpSender::DoSend, this); }
  void DoSend () {
    Ptr<Packet> p = Create<Packet> (m_bytes);
    StampTag tag; tag.t = Simulator::Now (); tag.sender = GetNode ()->GetId ();
    p->AddPacketTag (tag);
    g_txLog << Simulator::Now().GetSeconds() << "," << GetNode()->GetId() << "," << m_bytes << "\n";
    m_sock->Send (p);
    ScheduleTx ();
  }
  void StopApplication () override { if (m_sock) m_sock->Close (); }
  Ptr<Socket> m_sock; Address m_dst; uint32_t m_pps{200}, m_bytes{400}; Time m_interval;
};

class UdpReceiver : public Application {
public: void Setup (uint16_t port) { m_port = port; }
private:
  void StartApplication () override {
    m_sock = Socket::CreateSocket (GetNode (), UdpSocketFactory::GetTypeId ());
    m_sock->Bind (InetSocketAddress (Ipv4Address::GetAny (), m_port));
    m_sock->SetRecvCallback (MakeCallback (&UdpReceiver::HandleRead, this));
  }
  void HandleRead (Ptr<Socket> s) {
    Address from; Ptr<Packet> p;
    while ((p = s->RecvFrom (from))) {
      StampTag tag; Time sent = Seconds (0); uint32_t sender = UINT32_MAX;
      if (p->PeekPacketTag (tag)) { sent = tag.t; sender = tag.sender; }
      double dms = (Simulator::Now () - sent).GetMilliSeconds ();
      g_rxLog << Simulator::Now().GetSeconds() << "," << GetNode()->GetId()
              << "," << sender << "," << p->GetSize() << "," << dms << "\n";
    }
  }
  void StopApplication () override { if (m_sock) m_sock->Close (); }
  Ptr<Socket> m_sock; uint16_t m_port{5000};
};

int main (int argc, char** argv)
{
  // ---- CLI
  std::string calendarPath="none", outDir="ns3-output";
  uint32_t seed=1, runId=1, nNodes=10, pps=200, pktSize=400, simSeconds=600;
  CommandLine cmd;
  cmd.AddValue("calendar","Calendar path (used post-hoc)",calendarPath);
  cmd.AddValue("outDir","Output directory",outDir);
  cmd.AddValue("seed","RNG seed",seed);
  cmd.AddValue("runId","Run id",runId);
  cmd.AddValue("nNodes","# adhoc nodes",nNodes);
  cmd.AddValue("pps","Packets/s per sender",pps);
  cmd.AddValue("pktSize","Payload bytes",pktSize);
  cmd.AddValue("simSeconds","Duration (s)",simSeconds);
  cmd.Parse(argc, argv);
  RngSeedManager::SetSeed(seed); RngSeedManager::SetRun(runId);

  // ---- Output files
  std::string base = outDir + "/run_" + std::to_string(runId);
  g_txLog.open    (base + "_tx.csv");    g_txLog << "t_s,node,bytes\n";
  g_rxLog.open    (base + "_rx.csv");    g_rxLog << "t_s,node,peer,bytes,delay_ms\n";
  g_radioLog.open (base + "_radio.csv"); g_radioLog<< "t_s,freq_mhz,signal_dbm,noise_dbm\n";
  g_queueLog.open (base + "_queue.csv"); g_queueLog<< "t_s,dev,qlen,qdrops\n";

  // ---- Nodes + Wi-Fi adhoc
  NodeContainer nodes; nodes.Create(nNodes);
  YansWifiChannelHelper chan = YansWifiChannelHelper::Default();
  YansWifiPhyHelper phy; phy.SetChannel(chan.Create());
  phy.Set("ChannelSettings", StringValue("{0,20,BAND_2_4_GH,0}"));
  phy.Set("TxPowerStart", DoubleValue(16)); phy.Set("TxPowerEnd", DoubleValue(16));
  WifiHelper wifi; wifi.SetStandard(WIFI_STANDARD_80211g);
  wifi.SetRemoteStationManager("ns3::MinstrelHtWifiManager");
  WifiMacHelper mac; mac.SetType("ns3::AdhocWifiMac");
  NetDeviceContainer devs = wifi.Install(phy, mac, nodes);

  // radio sniffer
  Config::ConnectWithoutContext(
    "/NodeList/*/DeviceList/*/$ns3::WifiNetDevice/Phy/MonitorSnifferRx",
    MakeCallback(&MonitorSnifferRx));

  // ---- Mobility
  MobilityHelper mob;
  Ptr<ListPositionAllocator> pos = CreateObject<ListPositionAllocator>();
  double step=10.0; for (uint32_t i=0;i<nNodes;i++) pos->Add(Vector((i%5)*step,(i/5)*step,0));
  mob.SetPositionAllocator(pos);
  mob.SetMobilityModel("ns3::RandomWalk2dMobilityModel",
                       "Bounds", RectangleValue(Rectangle(-50,50,-50,50)),
                       "Speed", StringValue("ns3::ConstantRandomVariable[Constant=1.5]"));
  mob.Install(nodes);

  // ---- Internet + IPs
  InternetStackHelper internet; internet.Install(nodes);
  Ipv4AddressHelper ipv4; ipv4.SetBase("10.0.0.0","255.255.255.0");
  Ipv4InterfaceContainer ifs = ipv4.Assign(devs);

  // ---- Traffic control: FQ-CoDel with MaxSize="100p"
  // ---- Traffic control: use existing root qdisc if present, otherwise install FQ-CoDel
TrafficControlHelper tch;
tch.SetRootQueueDisc("ns3::FqCoDelQueueDisc",
                     "MaxSize", StringValue(std::to_string(g_qMaxPackets) + "p"));

g_qLen.assign(devs.GetN(), 0);
g_qDrops.assign(devs.GetN(), 0);

for (uint32_t i = 0; i < devs.GetN(); ++i) {
  Ptr<NetDevice> nd = devs.Get(i);
  Ptr<Node> node = nd->GetNode();
  Ptr<TrafficControlLayer> tc = node->GetObject<TrafficControlLayer>();
  Ptr<QueueDisc> root = (tc ? tc->GetRootQueueDiscOnDevice(nd) : nullptr);

  if (!root) {
    // no root qdisc yet: install FQ-CoDel on this device only
    QueueDiscContainer qdc = tch.Install(nd);
    root = qdc.Get(0);
  }

  // hook traces on the root qdisc (installed or preexisting)
  root->TraceConnectWithoutContext("Enqueue", MakeBoundCallback(&OnEnqueue, i));
  root->TraceConnectWithoutContext("Dequeue", MakeBoundCallback(&OnDequeue, i));
  root->TraceConnectWithoutContext("Drop",    MakeBoundCallback(&OnDrop,    i));
}


  // ---- Apps: pair senders/receivers
  uint16_t port = 5000;
  for (uint32_t i=0;i<nNodes/2;i++) {
    Ptr<UdpSender> s = CreateObject<UdpSender>();
    s->Setup(ifs.GetAddress(nNodes-1-i), port, pps, pktSize);
    nodes.Get(i)->AddApplication(s); s->SetStartTime(Seconds(1.0)); s->SetStopTime(Seconds(simSeconds));
    Ptr<UdpReceiver> r = CreateObject<UdpReceiver>(); r->Setup(port);
    nodes.Get(nNodes-1-i)->AddApplication(r); r->SetStartTime(Seconds(0.5)); r->SetStopTime(Seconds(simSeconds+1));
  }

  Simulator::Stop(Seconds(simSeconds));
  Simulator::Run();
  Simulator::Destroy();

  g_txLog.close(); g_rxLog.close(); g_radioLog.close(); g_queueLog.close();
  std::cout << "[plc_scenario] wrote " << base << "_{tx,rx,radio,queue}.csv\n";
  return 0;
}

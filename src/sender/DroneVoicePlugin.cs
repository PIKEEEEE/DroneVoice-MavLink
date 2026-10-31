using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Linq;
using System.Threading;
using System.Windows.Forms;
using Concentus.Enums;
using Concentus.Structs;
using MissionPlanner;
using MissionPlanner.Plugin;
using NAudio.Wave;

// Build against the DLLs from the installed Mission Planner version.
// Place DLL + NuGet dependency DLLs in the Mission Planner plugins directory.
public class DroneVoicePlugin : Plugin
{
    private ToolStripMenuItem menu;
    private VoiceWindow window;
    public override string Name { get { return "Drone Voice HM30"; } }
    public override string Version { get { return "0.4.0"; } }
    public override string Author { get { return "Reference implementation"; } }
    public override bool Init() { return true; }
    public override bool Loaded()
    {
        Host.MainForm.BeginInvoke((Action)(() => {
            menu = new ToolStripMenuItem("Drone Voice (PTT)...");
            menu.Click += (s, e) => ShowWindow();
            Host.FDMenuMap.Items.Add(menu); // Flight Data MAP right-click menu
            ShowWindow();
        }));
        return true;
    }
    private void ShowWindow()
    {
        if (window == null || window.IsDisposed)
            window = new VoiceWindow(Host);
        window.Show(Host.MainForm);
        window.BringToFront();
    }
    public override bool Loop() { return true; }
    public override bool Exit()
    {
        try {
            Host.MainForm.BeginInvoke((Action)(() => {
                if (window != null && !window.IsDisposed) window.Close();
                if (menu != null) Host.FDMenuMap.Items.Remove(menu);
            }));
        } catch { }
        return true;
    }
}

internal sealed class VoiceWindow : Form
{
    private const int Rate = 16000;
    private const int Samples = 320; // 20ms
    private const int OpusMax = 120;  // TUNNEL 128 - application header 8
    private readonly PluginHost host;
    private readonly Button ptt = new Button();
    private readonly Label status = new Label();
    private readonly ProgressBar micLevelBar = new ProgressBar();
    private readonly Label micLevelLabel = new Label();
    private readonly Label micHint = new Label();
    private int audioCallbacks; // increases when Windows supplies PCM audio
    private string micError = "";
    private int latestMicPeak;  // peak 0..100; updated by NAudio thread
    private int displayedMicPeak; // UI thread only
    private int clippingSamples; // exchange from NAudio thread to UI thread
    private readonly NumericUpDown targetSys = new NumericUpDown();
    private readonly NumericUpDown targetComp = new NumericUpDown();
    private readonly BlockingCollection<byte[]> frames = new BlockingCollection<byte[]>(new ConcurrentQueue<byte[]>(), 12);
    private readonly object audioLock = new object();
    private readonly byte[] raw = new byte[Samples * 2];
    private int rawCount;
    private WaveInEvent mic;
    private OpusEncoder encoder;
    private Thread sender;
    private volatile bool talking;
    private volatile bool pttHeld;
    private bool spaceHeld;
    private string pttInfo = "PTT: RELEASED";
    private volatile bool closing;
    private volatile int packets;
    private volatile int dropped;
    private ushort sequence;
    private volatile int selectedSys = 1;
    private volatile int selectedComp = 191;
    private readonly Stopwatch clock = Stopwatch.StartNew();
    private readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();

    public VoiceWindow(PluginHost host)
    {
        this.host = host;
        Text = "Mission Planner - Drone Voice PTT v0.4 + Mic Diagnostics";
        Width = 420; Height = 294;
        FormBorderStyle = FormBorderStyle.FixedToolWindow;
        KeyPreview = true;
        var labelSys = new Label { Left = 12, Top = 17, Width = 75, Text = "Target SYS" };
        targetSys.SetBounds(90, 12, 65, 24); targetSys.Minimum = 1; targetSys.Maximum = 255;
        targetSys.Value = (decimal)Math.Max(1, (int)host.comPort.MAV.sysid);
        var labelComp = new Label { Left = 174, Top = 17, Width = 90, Text = "Target COMP" };
        targetComp.SetBounds(267, 12, 65, 24); targetComp.Minimum = 1; targetComp.Maximum = 255; targetComp.Value = 191;
        selectedSys = (int)targetSys.Value;
        selectedComp = (int)targetComp.Value;
        targetSys.ValueChanged += (s, e) => selectedSys = (int)targetSys.Value;
        targetComp.ValueChanged += (s, e) => selectedComp = (int)targetComp.Value;
        ptt.Text = "HOLD TO TALK (mouse / space)";
        ptt.SetBounds(15, 48, 375, 76);
        // PTT must not stop merely because the pointer leaves the button.
        ptt.MouseDown += (s, e) => { if (e.Button == MouseButtons.Left) SetPTT(true); };
        ptt.MouseUp += (s, e) => { if (e.Button == MouseButtons.Left) SetPTT(false); };
        // Also allow SPACE while this form is focused (mouse PTT is primary).
        KeyDown += (s, e) => { if (e.KeyCode == Keys.Space) { spaceHeld = true; SetPTT(true); e.Handled = true; e.SuppressKeyPress = true; } };
        KeyUp += (s, e) => { if (e.KeyCode == Keys.Space) { spaceHeld = false; SetPTT(false); e.Handled = true; } };
        Deactivate += (s, e) => { spaceHeld = false; SetPTT(false); };
        micLevelLabel.SetBounds(15, 136, 375, 18);
        micLevelLabel.Text = "Mic Level: 0%";
        micLevelBar.SetBounds(15, 158, 375, 22);
        micLevelBar.Minimum = 0;
        micLevelBar.Maximum = 100;
        micLevelBar.Style = ProgressBarStyle.Continuous;
        micHint.SetBounds(15, 185, 375, 19);
        micHint.Text = "마이크 진단: PTT 없이 말해도 볼륨 바가 움직입니다.";
        status.SetBounds(15, 211, 375, 42);
        status.Text = "Starting microphone...";
        Controls.AddRange(new Control[] { labelSys, targetSys, labelComp, targetComp,
                                           ptt, micLevelLabel, micLevelBar, micHint, status });
        FormClosed += (s, e) => Shutdown();
        timer.Interval = 50; // UI meter update: 20 Hz
        timer.Tick += (s, e) => {
            // Peak collection and UI run on separate threads. Never access controls
            // from the NAudio callback.
            int peak = Interlocked.Exchange(ref latestMicPeak, 0);
            // Always display the physical microphone level, including without an MAVLink link.
            displayedMicPeak = peak >= displayedMicPeak
                ? peak : Math.Max(0, displayedMicPeak - 5);
            micLevelBar.Value = Math.Max(0, Math.Min(100, displayedMicPeak));
            micLevelLabel.Text = "Mic Level: " + micLevelBar.Value + "%";
            // Failsafe for a lost MouseUp (e.g., released outside form).
            if (pttHeld && (Control.MouseButtons & MouseButtons.Left) == 0
                && !spaceHeld) SetPTT(false);
            bool clip = Interlocked.Exchange(ref clippingSamples, 0) != 0;
            micHint.Text = clip ? "CLIPPING - 마이크 입력 볼륨을 낮추세요."
                : "마이크 진단: 연결 전에도 말하면 볼륨 바가 움직여야 합니다.";
            bool linked = false;
            try { linked = host.comPort != null && host.comPort.BaseStream != null && host.comPort.BaseStream.IsOpen; }
            catch { }
            status.Text = micError.Length != 0 ? "MIC ERROR: " + micError
                : string.Format("{0} | MAVLink={1} | TX={2} drop={3}\nMIC callbacks={4}",
                    pttInfo, linked ? "OPEN" : "CLOSED", packets, dropped,
                    Interlocked.CompareExchange(ref audioCallbacks, 0, 0));
        };
        try
        {
            encoder = OpusEncoder.Create(Rate, 1, OpusApplication.OPUS_APPLICATION_VOIP);
            encoder.Bitrate = 12000;
            encoder.UseVBR = false;
            mic = new WaveInEvent { WaveFormat = new WaveFormat(Rate, 16, 1), BufferMilliseconds = 20, NumberOfBuffers = 4 };
            mic.DataAvailable += OnAudio;
            mic.StartRecording();
            sender = new Thread(SenderLoop) { IsBackground = true, Name = "DroneVoice-MAVLinkTX" };
            sender.Start();
            timer.Start();
        }
        catch (Exception ex)
        {
            ptt.Enabled = false;
            micError = ex.Message;
            status.Text = "Audio initialization failed: " + ex.Message;
            timer.Start();
        }
    }

    // PTT state is always visible, even if the MAVLink connection is closed.
    private void SetPTT(bool pressed)
    {
        if (closing) return;
        if (pressed)
        {
            if (pttHeld) return;
            pttHeld = true;
            StartTalk();
        }
        else
        {
            if (!pttHeld && !talking) return;
            pttHeld = false;
            StopTalk();
        }
    }
    private void StartTalk()
    {
        ptt.BackColor = System.Drawing.Color.Khaki;
        ptt.Text = "PTT PRESSED";
        if (closing || mic == null || encoder == null)
        {
            pttInfo = "PTT PRESSED / MIC NOT READY";
            return;
        }
        bool linked = false;
        try { linked = host.comPort != null && host.comPort.BaseStream != null && host.comPort.BaseStream.IsOpen; }
        catch { }
        if (!linked)
        {
            pttInfo = "PTT PRESSED / MAVLink CLOSED (NO TX)";
            return;
        }
        // Discard stale queued voice frames from a previous PTT cycle.
        byte[] old;
        while (frames.TryTake(out old)) { }
        lock (audioLock) { rawCount = 0; talking = true; }
        ptt.BackColor = System.Drawing.Color.LightGreen;
        ptt.Text = "TRANSMITTING - RELEASE TO STOP";
        pttInfo = "PTT ON / TRANSMITTING";
    }
    private void StopTalk()
    {
        bool wasTalking;
        lock (audioLock) { wasTalking = talking; talking = false; rawCount = 0; }
        // Drop queued audio immediately, avoiding speech after PTT release.
        byte[] old;
        while (frames.TryTake(out old)) { }
        ptt.BackColor = System.Drawing.SystemColors.Control;
        ptt.Text = "HOLD TO TALK (mouse / space)";
        pttInfo = "PTT: RELEASED";
        if (wasTalking) Enqueue(new byte[0], 1); // end-of-talk marker
    }
    private void OnAudio(object sender, WaveInEventArgs e)
    {
        if (closing) return;
        Interlocked.Increment(ref audioCallbacks);
        UpdateMicMeter(e.Buffer, e.BytesRecorded);
        lock (audioLock)
        {
            if (!talking) { rawCount = 0; return; }
            int pos = 0;
            while (pos < e.BytesRecorded)
            {
                int n = Math.Min(raw.Length - rawCount, e.BytesRecorded - pos);
                Buffer.BlockCopy(e.Buffer, pos, raw, rawCount, n);
                rawCount += n; pos += n;
                if (rawCount != raw.Length) continue;
                var pcm = new short[Samples];
                Buffer.BlockCopy(raw, 0, pcm, 0, raw.Length);
                rawCount = 0;
                try
                {
                    var encoded = new byte[OpusMax];
                    int size = encoder.Encode(pcm, 0, Samples, encoded, 0, encoded.Length);
                    var opus = new byte[size];
                    Buffer.BlockCopy(encoded, 0, opus, 0, size);
                    Enqueue(opus, 0);
                }
                catch { Interlocked.Increment(ref dropped); }
            }
        }
    }
    // WaveInEvent is configured for signed, 16-bit little-endian mono PCM.
    // Keep the audio callback lightweight and do not touch WinForms controls here.
    private void UpdateMicMeter(byte[] buffer, int bytesRecorded)
    {
        int maxSample = 0;
        int usableBytes = Math.Min(bytesRecorded, buffer.Length) & ~1;
        for (int i = 0; i < usableBytes; i += 2)
        {
            int sample = (short)(buffer[i] | (buffer[i + 1] << 8));
            int abs = Math.Abs(sample);
            if (abs > maxSample) maxSample = abs;
        }
        if (maxSample >= 32000) Interlocked.Exchange(ref clippingSamples, 1);
        // Peak-dB display: -48 dBFS maps to 0%, 0 dBFS maps to 100%.
        // This gives visible response to ordinary speech at low input gain.
        double dB = maxSample > 0 ? 20.0 * Math.Log10(maxSample / 32768.0) : -96.0;
        int level = Math.Max(0, Math.Min(100, (int)Math.Round((dB + 48.0) * 100.0 / 48.0)));
        int current;
        do {
            current = Volatile.Read(ref latestMicPeak);
            if (current >= level) break;
        } while (Interlocked.CompareExchange(ref latestMicPeak, level, current) != current);
    }

    private void Enqueue(byte[] opus, byte flags)
    {
        byte[] data = new byte[128];
        ushort seq = sequence++;
        uint stamp = (uint)clock.ElapsedMilliseconds;
        data[0] = 1;
        data[1] = (byte)seq;
        data[2] = (byte)(seq >> 8);
        data[3] = (byte)stamp;
        data[4] = (byte)(stamp >> 8);
        data[5] = (byte)(stamp >> 16);
        data[6] = (byte)(stamp >> 24);
        data[7] = flags;
        Buffer.BlockCopy(opus, 0, data, 8, opus.Length);
        // Preserve actual length as first element in queue wrapper.
        var item = new byte[data.Length + 1];
        item[0] = (byte)(8 + opus.Length);
        Buffer.BlockCopy(data, 0, item, 1, data.Length);
        if (!frames.TryAdd(item)) Interlocked.Increment(ref dropped);
    }
    private void SenderLoop()
    {
        while (!closing)
        {
            byte[] item;
            if (!frames.TryTake(out item, 100)) continue;
            try
            {
                if (!host.comPort.BaseStream.IsOpen) { Interlocked.Increment(ref dropped); continue; }
                // A frame dequeued just before release must not be transmitted.
                bool isEndFrame = (item[1 + 7] & 1) != 0;
                if (!talking && !isEndFrame) continue;
                byte sys = (byte)selectedSys, comp = (byte)selectedComp;
                var payload = new byte[128];
                Buffer.BlockCopy(item, 1, payload, 0, 128);
                var msg = new MAVLink.mavlink_tunnel_t {
                    target_system = sys,
                    target_component = comp,
                    payload_type = 32768,
                    payload_length = item[0],
                    payload = payload
                };
                // Uses Mission Planner's active COM/UDP link; DO NOT reopen the port.
                host.comPort.sendPacket(msg, sys, comp);
                Interlocked.Increment(ref packets);
            }
            catch { Interlocked.Increment(ref dropped); }
        }
    }
    private void Shutdown()
    {
        StopTalk(); closing = true; timer.Stop();
        if (mic != null) { try { mic.StopRecording(); } catch { } mic.Dispose(); mic = null; }
        if (sender != null && sender.IsAlive) sender.Join(1000);
        frames.Dispose();
    }
}

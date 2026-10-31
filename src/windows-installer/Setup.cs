using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Windows.Forms;
using System.Xml.Linq;

[assembly: AssemblyTitle("Drone Voice Mission Planner Setup")]
[assembly: AssemblyProduct("Drone Voice Mission Planner Plugin")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

internal static class Engine
{
    public static readonly string[] Names = { "DroneVoicePlugin.dll", "NAudio.Core.dll", "NAudio.WinMM.dll", "Concentus.dll", "Microsoft.Win32.Registry.dll", "System.Security.AccessControl.dll", "System.Security.Principal.Windows.dll" };
    public static readonly Dictionary<string,string> Expected = new Dictionary<string,string> {
        {"DroneVoicePlugin.dll", "73C0A22BCA55FF9C26A12568FB76EB7FC6D51D56A6CF56098B8C3DBC10B16434"},
        {"NAudio.Core.dll", "FCF493FC47A2F478A65303886B975FBDBF714CBB1F2D79F7FCE97E4BB16B01A8"},
        {"NAudio.WinMM.dll", "749A01EBBB5EDD8B1A03C5263B04DE6ACADECF52E4CC84D7412BC6E93F180958"},
        {"Concentus.dll", "B536A1E869D447147333864451C51CEAB4804957857DD97914751FBA32EBE0CC"},
        {"Microsoft.Win32.Registry.dll", "619737E2AF8FB713085726631DD2E522FE130CAC1D388A59C38907A47D7AADEA"},
        {"System.Security.AccessControl.dll", "ACED52254A8C3CB6AD30F99F8B745296926C49373CAB00824C2C4C10AD325B10"},
        {"System.Security.Principal.Windows.dll", "C3AE599B5218516915F6542779EF577D5AD5DE3D84A116E671C09E15E4591285"}
    };
    public static byte[] Resource(string name)
    {
        using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream("Payload." + name))
        {
            if (s == null) throw new IOException("Missing embedded file: " + name);
            using (MemoryStream m = new MemoryStream()) { s.CopyTo(m); return m.ToArray(); }
        }
    }
    public static string Hash(byte[] data)
    {
        using (SHA256 s = SHA256.Create()) return BitConverter.ToString(s.ComputeHash(data)).Replace("-", "");
    }
    public static string FileHash(string path) { return Hash(File.ReadAllBytes(path)); }
    public static string Validate(string path)
    {
        string root = Path.GetFullPath(path.Trim().Trim('"')).TrimEnd(Path.DirectorySeparatorChar);
        foreach (string name in new [] {"MissionPlanner.exe", "MAVLink.dll", "MissionPlanner.ArduPilot.dll", "MissionPlanner.Utilities.dll", "Interfaces.dll"})
            if (!File.Exists(Path.Combine(root, name))) throw new IOException("미션 플래너 설치 폴더가 아닙니다. 파일 없음: " + name);
        Version host = AssemblyName.GetAssemblyName(Path.Combine(root,"MissionPlanner.exe")).Version;
        if (host.ToString() != "1.3.9384.38250")
            throw new IOException("이 설치본은 Mission Planner 1.3.9384.38250 기준입니다.\n선택한 버전: " + host + "\n다른 버전은 호환 확인 후 다시 빌드해야 합니다.");
        return root;
    }
    public static void CheckStopped()
    {
        if (Process.GetProcessesByName("MissionPlanner").Length != 0)
            throw new IOException("미션 플래너를 완전히 종료한 뒤 다시 시도해 주세요.");
    }
    static string State(string root) { return Path.Combine(root,"plugins",".dronevoice-setup"); }
    static XDocument ReadReceipt(string root)
    {
        string path = Path.Combine(State(root),"receipt.xml");
        return File.Exists(path) ? XDocument.Load(path) : new XDocument(new XElement("DroneVoice"));
    }
    static void ValidateReceipt(XDocument doc)
    {
        if (doc.Root == null || doc.Root.Name != "DroneVoice") throw new IOException("Invalid installation receipt");
        foreach (XElement e in doc.Root.Elements("File"))
        {
            string name = (string)e.Attribute("name");
            if (!Names.Contains(name)) throw new IOException("Invalid receipt filename");
        }
    }
    public static string Install(string path)
    {
        string root = Validate(path), plugins = Path.Combine(root,"plugins");
        string state = State(root), original = Path.Combine(state,"original");
        XDocument receipt = ReadReceipt(root); ValidateReceipt(receipt);
        var payload = new Dictionary<string,byte[]>();
        var changes = new List<string>();
        // Validate the entire plan before changing any plugin file.
        foreach (string name in Names)
        {
            byte[] bytes = Resource(name);
            if (Hash(bytes) != Expected[name]) throw new IOException("Embedded checksum mismatch: " + name);
            payload[name] = bytes;
            string destination = Path.Combine(plugins,name);
            XElement old = receipt.Root.Elements("File").FirstOrDefault(e => (string)e.Attribute("name") == name);
            if (old != null && File.Exists(destination) && FileHash(destination) != (string)old.Attribute("installedHash"))
                throw new IOException("설치 후 변경된 파일이 있어 덮어쓰지 않습니다: " + name);
            if (name != "DroneVoicePlugin.dll")
            {
                string shared = Path.Combine(root,name);
                if (File.Exists(shared))
                {
                    if (FileHash(shared) != Expected[name])
                        throw new IOException("미션 플래너 공용 라이브러리 버전이 다릅니다: " + name + "\n공용 파일은 덮어쓰지 않았습니다.");
                    if (!File.Exists(destination)) continue;
                }
                if (File.Exists(destination) && FileHash(destination) != Expected[name] && old == null)
                    throw new IOException("다른 플러그인의 라이브러리와 충돌할 수 있어 중단했습니다: " + name);
            }
            if (File.Exists(destination) && FileHash(destination) == Expected[name]) continue;
            changes.Add(name);
        }
        Directory.CreateDirectory(plugins); Directory.CreateDirectory(original);
        var before = new Dictionary<string,byte[]>();
        var applied = new List<string>();
        try
        {
            foreach (string name in changes)
            {
                string destination = Path.Combine(plugins,name);
                before[name] = File.Exists(destination) ? File.ReadAllBytes(destination) : null;
                XElement entry = receipt.Root.Elements("File").FirstOrDefault(e => (string)e.Attribute("name") == name);
                if (entry == null)
                {
                    entry = new XElement("File",new XAttribute("name",name),new XAttribute("hadOriginal",before[name] != null));
                    if (before[name] != null)
                    {
                        File.WriteAllBytes(Path.Combine(original,name),before[name]);
                        entry.SetAttributeValue("originalHash",Hash(before[name]));
                    }
                    receipt.Root.Add(entry);
                }
                applied.Add(name);
                File.WriteAllBytes(destination,payload[name]);
                if (FileHash(destination) != Expected[name]) throw new IOException("Installed checksum mismatch: " + name);
                entry.SetAttributeValue("installedHash",Expected[name]);
            }
            File.WriteAllBytes(Path.Combine(state,"THIRD-PARTY-NOTICES.txt"),Resource("THIRD-PARTY-NOTICES.txt"));
            string temp = Path.Combine(state,"receipt.tmp"); receipt.Save(temp);
            string receiptPath = Path.Combine(state,"receipt.xml");
            if (File.Exists(receiptPath)) File.Replace(temp,receiptPath,null); else File.Move(temp,receiptPath);
        }
        catch
        {
            foreach (string name in applied.AsEnumerable().Reverse())
            {
                string destination = Path.Combine(plugins,name);
                if (before[name] == null) File.Delete(destination); else File.WriteAllBytes(destination,before[name]);
            }
            throw;
        }
        return changes.Count == 0 ? "이미 같은 버전이 설치되어 있습니다." : "설치 완료!\n미션 플래너를 실행하세요.\nTarget SYS=1, COMP=191로 맞추고 PTT를 누른 채 말하세요.";
    }
    public static string Restore(string path)
    {
        string root=Validate(path), state=State(root), plugins=Path.Combine(root,"plugins");
        if (!File.Exists(Path.Combine(state,"receipt.xml"))) return "이 설치 프로그램의 설치 기록이 없습니다.";
        XDocument receipt=ReadReceipt(root); ValidateReceipt(receipt);
        var entries=receipt.Root.Elements("File").ToList();
        foreach (XElement e in entries)
        {
            string name=(string)e.Attribute("name"), file=Path.Combine(plugins,name);
            if (File.Exists(file) && FileHash(file)!=(string)e.Attribute("installedHash"))
                throw new IOException("설치 후 변경된 파일은 보존합니다. 자동 복원을 중단했습니다: "+name);
            if ((bool)e.Attribute("hadOriginal"))
            {
                string backup=Path.Combine(state,"original",name);
                if (!File.Exists(backup) || FileHash(backup)!=(string)e.Attribute("originalHash"))
                    throw new IOException("원본 백업 확인 실패: "+name);
            }
        }
        var before = new Dictionary<string,byte[]>();
        var applied = new List<string>();
        try
        {
            foreach (XElement e in entries)
            {
                string name=(string)e.Attribute("name"), file=Path.Combine(plugins,name);
                before[name]=File.Exists(file)?File.ReadAllBytes(file):null;
                applied.Add(name);
                if ((bool)e.Attribute("hadOriginal")) File.Copy(Path.Combine(state,"original",name),file,true);
                else if (File.Exists(file)) File.Delete(file);
            }
            File.Delete(Path.Combine(state,"receipt.xml"));
        }
        catch
        {
            foreach (string name in applied.AsEnumerable().Reverse())
            {
                string file=Path.Combine(plugins,name);
                if (before[name]==null) File.Delete(file); else File.WriteAllBytes(file,before[name]);
            }
            throw;
        }
        return "설치 전 상태로 복원했습니다.\n이전에 다른 Drone Voice 플러그인이 있었다면 그 버전으로 돌아갑니다.";
    }
}

internal sealed class SetupWindow : Form
{
    TextBox folder = new TextBox();
    Label result = new Label();
    public SetupWindow()
    {
        Text="Drone Voice — Mission Planner 설치";
        ClientSize=new System.Drawing.Size(700,340);
        FormBorderStyle=FormBorderStyle.FixedDialog; MaximizeBox=false;
        StartPosition=FormStartPosition.CenterScreen;
        Font=new System.Drawing.Font("맑은 고딕",10);
        Controls.Add(new Label {Left=20,Top=18,Width=660,Height=60,
            Text="Drone Voice PTT 플러그인 설치\n미션 플래너를 종료한 뒤 설치 폴더를 확인해 주세요."});
        folder.SetBounds(20,88,550,28);
        string defaultRoot=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),"Mission Planner");
        folder.Text=defaultRoot;
        Button browse=new Button {Text="찾아보기",Left=580,Top=86,Width=100,Height=32};
        browse.Click+=(s,e)=> { using (var picker=new FolderBrowserDialog()) {
            picker.Description="MissionPlanner.exe가 있는 폴더를 선택하세요.";
            if (Directory.Exists(folder.Text)) picker.SelectedPath=folder.Text;
            if(picker.ShowDialog()==DialogResult.OK) folder.Text=picker.SelectedPath;
        }};
        Button install=new Button {Text="설치 / 업데이트",Left=20,Top=135,Width=190,Height=42};
        install.Click+=(s,e)=> Perform(false);
        Button restore=new Button {Text="설치 전 상태 복원",Left=225,Top=135,Width=190,Height=42};
        restore.Click+=(s,e)=>Perform(true);
        Button license=new Button {Text="라이선스",Left=430,Top=135,Width=120,Height=42};
        license.Click+=(s,e)=> {
            Form f=new Form {Text="Third-party licenses",Width=820,Height=600,StartPosition=FormStartPosition.CenterParent};
            f.Controls.Add(new TextBox {Multiline=true,ReadOnly=true,ScrollBars=ScrollBars.Both,Dock=DockStyle.Fill,
                Text=Encoding.UTF8.GetString(Engine.Resource("THIRD-PARTY-NOTICES.txt"))}); f.ShowDialog(this); f.Dispose();
        };
        result.SetBounds(20,195,660,120);
        result.Text="설치 후 미션 플래너에서 Drone Voice 창이 열립니다.\nTarget SYS=1 / COMP=191 · 마우스 또는 스페이스 키를 누른 채 송신\n기존 플러그인은 백업하며 미션 플래너 공용 DLL은 변경하지 않습니다.";
        Controls.AddRange(new Control[] {folder,browse,install,restore,license,result});
    }
    void Perform(bool restore)
    {
        try
        {
            Engine.CheckStopped();
            if(restore && MessageBox.Show(this,"이 설치 프로그램이 바꾼 파일을 설치 전 상태로 복원할까요?","복원",MessageBoxButtons.YesNo)!=DialogResult.Yes) return;
            UseWaitCursor=true;
            result.Text=restore?Engine.Restore(folder.Text):Engine.Install(folder.Text);
            MessageBox.Show(this,result.Text,"Drone Voice");
        }
        catch(Exception ex) { result.Text=ex.Message; MessageBox.Show(this,ex.Message,"설치 확인",MessageBoxButtons.OK,MessageBoxIcon.Warning); }
        finally { UseWaitCursor=false; }
    }
}

internal static class Program
{
    [STAThread] static int Main(string[] args)
    {
#if TEST_BUILD
        if(args.Length==2 && args[0]=="--self-test") return SelfTest(args[1]);
#endif
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
#if TEST_BUILD
        if(args.Length==2 && args[0]=="--render") {
            using(var f=new SetupWindow()) using(var b=new System.Drawing.Bitmap(f.Width,f.Height)) {
                f.CreateControl(); f.DrawToBitmap(b,new System.Drawing.Rectangle(0,0,f.Width,f.Height));
                b.Save(args[1],System.Drawing.Imaging.ImageFormat.Png);
            }
            return 0;
        }
#endif
        Application.Run(new SetupWindow()); return 0;
    }
#if TEST_BUILD
    static void Assert(bool ok,string message) { if(!ok) throw new Exception(message); }
    static int SelfTest(string baseFolder)
    {
        // All write tests run solely inside the explicitly supplied workspace folder.
        string root=Path.GetFullPath(baseFolder); Directory.CreateDirectory(root);
        foreach(string name in new[]{"MissionPlanner.exe","MAVLink.dll","MissionPlanner.ArduPilot.dll","MissionPlanner.Utilities.dll","Interfaces.dll"})
            File.Copy(Path.Combine(Environment.GetEnvironmentVariable("DRONEVOICE_MP_DIR") ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),"Mission Planner"),name),Path.Combine(root,name),true);
        string plugins=Path.Combine(root,"plugins"); Directory.CreateDirectory(plugins);
        foreach(string name in Engine.Names) Assert(Engine.Hash(Engine.Resource(name))==Engine.Expected[name],"Resource checksum: "+name);
        Engine.Install(root);
        foreach(string name in Engine.Names) Assert(Engine.FileHash(Path.Combine(plugins,name))==Engine.Expected[name],"Installed file: "+name);
        Engine.Install(root);
        // Changed files are protected during rollback.
        string plugin=Path.Combine(plugins,"DroneVoicePlugin.dll"); File.AppendAllText(plugin,"changed");
        bool blocked=false; try{Engine.Restore(root);}catch(IOException){blocked=true;} Assert(blocked,"Changed-file protection");
        File.WriteAllBytes(plugin,Engine.Resource("DroneVoicePlugin.dll")); Engine.Restore(root);
        foreach(string name in Engine.Names) Assert(!File.Exists(Path.Combine(plugins,name)),"New-file removal: "+name);
        // Existing plugins are backed up and restored byte-for-byte.
        byte[] original=Encoding.UTF8.GetBytes("original plugin fixture"); File.WriteAllBytes(plugin,original);
        Engine.Install(root); Engine.Restore(root); Assert(Engine.FileHash(plugin)==Engine.Hash(original),"Original backup restore");
        // Root dependencies are reused and preserved.
        File.WriteAllBytes(Path.Combine(root,"NAudio.Core.dll"),Engine.Resource("NAudio.Core.dll"));
        Engine.Install(root); Assert(!File.Exists(Path.Combine(plugins,"NAudio.Core.dll")),"Reuse root dependency"); Engine.Restore(root);
        Assert(Engine.FileHash(Path.Combine(root,"NAudio.Core.dll"))==Engine.Expected["NAudio.Core.dll"],"Root unchanged");
        File.WriteAllText(Path.Combine(root,"NAudio.Core.dll"),"different dependency");
        blocked=false; try{Engine.Install(root);}catch(IOException){blocked=true;} Assert(blocked,"Dependency conflict protection");
        Assert(Engine.FileHash(plugin)==Engine.Hash(original),"Conflict leaves plugin untouched");
        File.WriteAllText(Path.Combine(root,"SELF_TEST_PASS.txt"),"Embedded hashes, install, reinstall, backup, restore, changed-file protection, shared dependency reuse/conflict: PASS");
        return 0;
    }
#endif
}

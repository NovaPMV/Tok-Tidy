// TokTidy.exe - a tiny launcher that lives in the TokTidy folder.
// It finds everything relative to its own location, so it keeps working
// after the whole folder is moved. setup.bat builds it with the C# compiler
// that ships with Windows (.NET Framework 4).
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("TokTidy")]
[assembly: AssemblyProduct("TokTidy")]
[assembly: AssemblyDescription("Opens TokTidy")]

static class Launcher
{
    [STAThread]
    static int Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
        string electron = Path.Combine(root, @"app\node_modules\electron\dist\electron.exe");
        string appDir = Path.Combine(root, "app");
        if (!File.Exists(electron) || !File.Exists(Path.Combine(root, @".venv\Scripts\python.exe")))
        {
            MessageBox.Show(
                "TokTidy isn't fully installed in this folder yet.\n\n" +
                "Double-click setup.bat in this folder, then try again.\n\n" + root,
                "TokTidy", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return 1;
        }
        try
        {
            var psi = new ProcessStartInfo(electron, "\"" + appDir + "\"");
            psi.WorkingDirectory = root;
            psi.UseShellExecute = false;
            Process.Start(psi);
            return 0;
        }
        catch (Exception e)
        {
            MessageBox.Show("Could not open TokTidy:\n\n" + e.Message, "TokTidy",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}

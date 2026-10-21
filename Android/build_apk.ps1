# Builds MotorcycleMan.apk without Gradle, using the JDK and Android SDK in tools\.
# Signs with build\debug.keystore so each new build installs as an update over the last one.
$ErrorActionPreference = "Stop"
$AndroidDir = $PSScriptRoot
# This PC keeps its JDK and SDK in tools\; GitHub's build machine has its own (JAVA_HOME, ANDROID_HOME).
$Jdk = if (Test-Path "$AndroidDir\tools\jdk17") { "$AndroidDir\tools\jdk17" } else { $env:JAVA_HOME }
$Sdk = if (Test-Path "$AndroidDir\tools\sdk") { "$AndroidDir\tools\sdk" } else { $env:ANDROID_HOME }
$BuildTools = "$Sdk\build-tools\34.0.0"
$AndroidJar = "$Sdk\platforms\android-34\android.jar"
$BuildDir = "$AndroidDir\build"
$Keystore = "$BuildDir\debug.keystore"
$env:JAVA_HOME = $Jdk
$env:PATH = "$Jdk\bin;$env:PATH"

function Invoke-Tool($exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "$(Split-Path $exe -Leaf) failed with exit code $LASTEXITCODE" }
}

Remove-Item "$BuildDir\classes", "$BuildDir\gen", "$BuildDir\classes.dex", "$BuildDir\classes.jar", "$BuildDir\resources.zip", "$BuildDir\*.apk" -Recurse -Force -ErrorAction SilentlyContinue -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$BuildDir\classes", "$BuildDir\gen" | Out-Null

# The launcher icons in res\ come from Icons\make_icons.py.
Invoke-Tool "$BuildTools\aapt2.exe" @("compile", "--dir", "$AndroidDir\res", "-o", "$BuildDir\resources.zip")

# Linked first, so R.java (the widget's layout and views) is there for the Java to use.
Invoke-Tool "$BuildTools\aapt2.exe" @("link", "-o", "$BuildDir\unaligned.apk", "-I", $AndroidJar, "--manifest", "$AndroidDir\AndroidManifest.xml", "--java", "$BuildDir\gen", "$BuildDir\resources.zip")
$sources = (Get-ChildItem "$AndroidDir\src", "$BuildDir\gen" -Recurse -Filter *.java).FullName
Invoke-Tool "$Jdk\bin\javac.exe" (@("-encoding", "UTF-8", "-source", "1.8", "-target", "1.8", "-nowarn", "-bootclasspath", $AndroidJar, "-d", "$BuildDir\classes") + $sources)
# In one jar: the list of class files grew too long for a Windows command line.
Invoke-Tool "$Jdk\bin\jar.exe" @("cf", "$BuildDir\classes.jar", "-C", "$BuildDir\classes", ".")
Invoke-Tool "$BuildTools\d8.bat" @("--lib", $AndroidJar, "--min-api", "24", "--output", $BuildDir, "$BuildDir\classes.jar")
# aapt2 doesn't package code; append classes.dex without touching the entries it wrote.
Invoke-Tool "python" @("-c", "import sys, zipfile; z = zipfile.ZipFile(sys.argv[1], 'a', zipfile.ZIP_DEFLATED); z.write(sys.argv[2], 'classes.dex'); z.close()", "$BuildDir\unaligned.apk", "$BuildDir\classes.dex")
Invoke-Tool "$BuildTools\zipalign.exe" @("-f", "-p", "4", "$BuildDir\unaligned.apk", "$BuildDir\aligned.apk")

if (-not (Test-Path $Keystore)) {
    Invoke-Tool "$Jdk\bin\keytool.exe" @("-genkeypair", "-keystore", $Keystore, "-storepass", "android", "-keypass", "android",
        "-alias", "androiddebugkey", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000", "-dname", "CN=Android Debug,O=Android,C=US")
    Write-Warning "Created a new signing key. Uninstall the old Motorcycle Man app from your phone before installing this build."
}
Invoke-Tool "$BuildTools\apksigner.bat" @("sign", "--ks", $Keystore, "--ks-pass", "pass:android", "--ks-key-alias", "androiddebugkey",
    "--key-pass", "pass:android", "--out", "$AndroidDir\MotorcycleMan.apk", "$BuildDir\aligned.apk")
Invoke-Tool "$BuildTools\apksigner.bat" @("verify", "$AndroidDir\MotorcycleMan.apk")
Write-Output "Built $AndroidDir\MotorcycleMan.apk"

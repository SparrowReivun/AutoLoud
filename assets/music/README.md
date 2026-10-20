# 校准音乐库 / Calibration music library

把你的音乐放到这里，每个风格一个子文件夹，例如：

```
assets/music/
  Pop/        track1.flac  track2.mp3
  Classical/  ...
  Rock/       ...
```

支持 wav / flac / ogg / mp3 / aiff。在「设置 → 校准音乐文件夹」里选中此目录
（或任何别的目录）。文件夹为空时，校准向导会使用合成的测试音乐。

`default/` 里是内置的默认校准曲目（McG-pro1.flac，约 -23 LUFS），会随 exe 一起打包；
其它文件不进入 git 仓库；用户安装后在设置里自行指定文件夹，或在校准时直接选用正在播放的应用。

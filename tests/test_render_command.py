from pathlib import Path

from clipstudio.render import Plan, build_command
from clipstudio.timeline import Timeline


def plan(**changes):
    base = dict(src=Path("source.mp4"), pieces=Timeline([(3.0, 10.0)]).pieces, length=7.0, layout="blur", band=608)
    base.update(changes)
    return Plan(**base)


def graph_of(args):
    return args[args.index("-filter_complex") + 1]


def inputs_of(args):
    return [args[i + 1] for i, a in enumerate(args) if a == "-i"]


def test_plain_clip():
    args, out = build_command(plan())
    g = graph_of(args)
    assert inputs_of(args) == ["source.mp4"]
    assert args[args.index("-ss") + 1] == "3.000" and args[args.index("-t") + 1] == "7.000"
    assert "concat" not in g and "[pv0]fps=30" in g
    assert g.endswith("[pa0]loudnorm=I=-14:TP=-1.5:LRA=11[aout]")
    assert args[-2:] == ["-map", "[v0]"] and out[:2] == ["-map", "[aout]"]


def test_pieces_are_joined_in_order():
    t = Timeline([(1.0, 2.0), (5.0, 6.5), (9.0, 9.5)])
    args, _ = build_command(plan(pieces=t.pieces, length=t.length))
    g = graph_of(args)
    assert inputs_of(args) == ["source.mp4"] * 3
    assert [args[i + 1] for i, a in enumerate(args) if a == "-ss"] == ["1.000", "5.000", "9.000"]
    assert "[pv0][pa0][pv1][pa1][pv2][pa2]concat=n=3:v=1:a=1[cv][ca]" in g
    assert "[cv]fps=30" in g


def test_no_sound_in_the_source():
    t = Timeline([(1.0, 2.0), (5.0, 6.5)])
    args, out = build_command(plan(pieces=t.pieces, length=t.length, has_audio=False))
    g = graph_of(args)
    assert "concat=n=2:v=1:a=0[cv]" in g and "[ca]" not in g and "loudnorm" not in g
    assert "-c:a" not in out


def test_zooms_overlay_a_scaled_copy_at_their_times():
    g = graph_of(build_command(plan(zooms=[{"start": 1.0, "end": 2.5}, {"start": 4.0, "end": 5.0}]))[0])
    assert "[zsrc]scale=1242:2208,crop=1080:1920[zin]" in g
    assert "enable='between(t,1.000,2.500)+between(t,4.000,5.000)'[vz]" in g


def test_emoji_captions_sfx_and_music_all_together():
    p = plan(emojis=[{"t": 2.0, "seconds": 1.4, "file": "fire_3d.png", "size": 190, "px": 445, "py": 1100}],
             captions_file="captions.ass",
             sfx_file="sfx.wav", music_file=Path("beat.mp3"), music_volume=0.2, zooms=[{"start": 1.0, "end": 2.0}])
    args, out = build_command(p)
    g = graph_of(args)
    assert inputs_of(args) == ["source.mp4", "sfx.wav", "beat.mp3", "fire_3d.png"]
    assert args[args.index("beat.mp3") - 3:args.index("beat.mp3")] == ["-stream_loop", "-1", "-i"]
    assert "[3:v]scale=190:190,format=rgba,fade=t=in:st=2.000" in g
    assert "[vz][e0]overlay=x=445:y=1100:enable='between(t,2.000,3.400)'[ve0]" in g
    assert "[ve0]ass=captions.ass:fontsdir=fonts[vout]" in g
    assert "volume=0.200" in g and "[mus][key]sidechaincompress" in g
    assert "[sp][mduck][sfx]amix=inputs=3:normalize=0" in g
    assert args[-2:] == ["-map", "[vout]"] and out[:2] == ["-map", "[aout]"]


def test_cuts_snap_to_whole_frames():
    from clipstudio.render import snap_to_frames
    assert snap_to_frames([(1.01, 2.52), (3.0, 3.01)], 30) == [(1.0, 2.5333)]     # the 1/100 s piece is gone
    assert snap_to_frames([(0.97, 2.0)], 25) == [(0.96, 2.0)]


def test_clip_volume_and_placed_emoji():
    t = Timeline([(1.0, 2.0, 0.0), (5.0, 6.0, 1.5), (9.0, 10.0)])
    g = graph_of(build_command(plan(pieces=t.pieces, length=t.length,
                                    emojis=[{"t": 0.5, "seconds": 1.0, "file": "x.png", "size": 300, "px": -20, "py": 80}]))[0])
    assert "atrim=duration=1.0000,volume=0.000," in g and "volume=1.500," in g
    assert g.count("volume=") == 2                      # the third clip is left alone
    assert "scale=300:300" in g and "overlay=x=-20:y=80:" in g


def test_music_without_speech():
    g = graph_of(build_command(plan(has_audio=False, music_file=Path("beat.mp3")))[0])
    assert "sidechaincompress" not in g and "[mus]loudnorm" in g

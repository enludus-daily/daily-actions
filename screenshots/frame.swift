// ストア用のスクリーンショットを作る（macOS の標準の部品だけ。外部の依存なし）。
// 使い方: swift frame.swift <撮った PNG> <出力 PNG> <見出し> <小見出し> <背景色 #RRGGBB> <文字色 #RRGGBB> [<幅> <高さ>]
// 既定の大きさは 1290 × 2796（App Store の 6.7 / 6.9 インチの iPhone、APP_IPHONE_67）。
// 上に見出し、下に角を丸めた画面を少し大きめに置き、下の端ははみ出させる。
import AppKit
import CoreGraphics
import CoreText
import Foundation
import ImageIO
import UniformTypeIdentifiers

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(("frame.swift: " + message + "\n").data(using: .utf8)!)
    exit(1)
}

func color(_ hex: String) -> CGColor {
    var s = hex.trimmingCharacters(in: .whitespaces)
    if s.hasPrefix("#") { s.removeFirst() }
    guard s.count == 6, let v = UInt32(s, radix: 16) else { fail("色は #RRGGBB: \(hex)") }
    return CGColor(red: CGFloat((v >> 16) & 0xFF) / 255, green: CGFloat((v >> 8) & 0xFF) / 255, blue: CGFloat(v & 0xFF) / 255, alpha: 1)
}

func mix(_ a: CGColor, _ b: CGColor, _ t: CGFloat) -> CGColor {
    let x = a.components ?? [0, 0, 0, 1], y = b.components ?? [0, 0, 0, 1]
    return CGColor(red: x[0] * (1 - t) + y[0] * t, green: x[1] * (1 - t) + y[1] * t, blue: x[2] * (1 - t) + y[2] * t, alpha: 1)
}

/// 丸ゴシックがあれば使う（ヒラギノ丸ゴ）。無ければシステムの太字
func font(_ size: CGFloat, bold: Bool) -> CTFont {
    let names = bold ? ["HiraMaruProN-W4", "HiraginoSans-W7"] : ["HiraMaruProN-W4", "HiraginoSans-W4"]
    for n in names {
        let f = CTFontCreateWithName(n as CFString, size, nil)
        if (CTFontCopyPostScriptName(f) as String) == n { return f }
    }
    return NSFont.systemFont(ofSize: size, weight: bold ? .heavy : .medium) as CTFont
}

/// 中央そろえで、幅に収まるよう折り返して描く。描いた高さを返す
@discardableResult
func drawText(_ ctx: CGContext, _ text: String, font f: CTFont, color c: CGColor, centerX: CGFloat, top: CGFloat, width: CGFloat, canvasHeight: CGFloat, lineSpacing: CGFloat = 1.15) -> CGFloat {
    guard !text.isEmpty else { return 0 }
    let style = NSMutableParagraphStyle()
    style.alignment = .center
    style.lineHeightMultiple = lineSpacing
    let attr = NSAttributedString(string: text, attributes: [
        .font: f, .foregroundColor: NSColor(cgColor: c) ?? .black, .paragraphStyle: style,
    ])
    let setter = CTFramesetterCreateWithAttributedString(attr)
    let size = CTFramesetterSuggestFrameSizeWithConstraints(setter, CFRange(location: 0, length: 0), nil, CGSize(width: width, height: .greatestFiniteMagnitude), nil)
    // CoreGraphics は下が 0。top（上からの距離）を変換する
    let rect = CGRect(x: centerX - width / 2, y: canvasHeight - top - size.height, width: width, height: ceil(size.height) + 2)
    let frame = CTFramesetterCreateFrame(setter, CFRange(location: 0, length: 0), CGPath(rect: rect, transform: nil), nil)
    CTFrameDraw(frame, ctx)
    return size.height
}

let args = CommandLine.arguments
guard args.count >= 7 else { fail("引数: <in.png> <out.png> <見出し> <小見出し> <背景色> <文字色> [幅 高さ]") }
let input = URL(fileURLWithPath: args[1]), output = URL(fileURLWithPath: args[2])
let title = args[3].replacingOccurrences(of: "\\n", with: "\n"), subtitle = args[4].replacingOccurrences(of: "\\n", with: "\n")
let bg = color(args[5]), ink = color(args[6])
let W = CGFloat(args.count >= 9 ? Double(args[7]) ?? 1290 : 1290), H = CGFloat(args.count >= 9 ? Double(args[8]) ?? 2796 : 2796)

guard let src = CGImageSourceCreateWithURL(input as CFURL, nil), let shot = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
    fail("画像を読めません: \(input.path)")
}
guard let ctx = CGContext(data: nil, width: Int(W), height: Int(H), bitsPerComponent: 8, bytesPerRow: 0,
                          space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else {
    fail("描く場所を作れません")
}

// 背景：上から下へ少しだけ明るく
let white = CGColor(red: 1, green: 1, blue: 1, alpha: 1)
let gradient = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB), colors: [mix(bg, white, 0.18), bg] as CFArray, locations: [0, 1])!
ctx.drawLinearGradient(gradient, start: CGPoint(x: 0, y: H), end: CGPoint(x: 0, y: 0), options: [])

// 見出し
let margin = W * 0.08
var y = H * 0.055
y += drawText(ctx, title, font: font(W * 0.075, bold: true), color: ink, centerX: W / 2, top: y, width: W - margin * 2, canvasHeight: H)
y += W * 0.02
y += drawText(ctx, subtitle, font: font(W * 0.038, bold: false), color: mix(ink, bg, 0.25), centerX: W / 2, top: y, width: W - margin * 2, canvasHeight: H)
y += W * 0.06

// 画面：幅 84%、角を丸めて縁と影をつけ、下ははみ出す
let shotW = W * 0.84
let shotH = shotW * CGFloat(shot.height) / CGFloat(shot.width)
let rect = CGRect(x: (W - shotW) / 2, y: H - y - shotH, width: shotW, height: shotH)
let radius = shotW * 0.075
let border = W * 0.012
let outer = CGPath(roundedRect: rect.insetBy(dx: -border, dy: -border), cornerWidth: radius + border, cornerHeight: radius + border, transform: nil)
ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -W * 0.012), blur: W * 0.04, color: CGColor(red: 0, green: 0, blue: 0, alpha: 0.28))
ctx.addPath(outer)
ctx.setFillColor(ink)
ctx.fillPath()
ctx.restoreGState()
ctx.saveGState()
ctx.addPath(CGPath(roundedRect: rect, cornerWidth: radius, cornerHeight: radius, transform: nil))
ctx.clip()
ctx.interpolationQuality = .high
ctx.draw(shot, in: rect)
ctx.restoreGState()

guard let image = ctx.makeImage(),
      let dest = CGImageDestinationCreateWithURL(output as CFURL, UTType.png.identifier as CFString, 1, nil) else {
    fail("書き出せません: \(output.path)")
}
CGImageDestinationAddImage(dest, image, nil)
guard CGImageDestinationFinalize(dest) else { fail("書き出せません: \(output.path)") }
print("frame.swift: \(output.lastPathComponent) \(Int(W))x\(Int(H))")

// 개발용: BepInEx 가 만든 interop 어셈블리에서 타입·필드·속성(형식 포함)·메서드 이름을 뽑는다.
// 어셈블리를 실행·로드하지 않고 메타데이터만 읽는다.
//   InteropDump.exe <dll> [이름 일부...]
using System.Collections.Immutable;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;

var path = args[0];
var patterns = args.Skip(1).Select(p => p.ToLowerInvariant()).ToArray();
using var fs = File.OpenRead(path);
using var pe = new PEReader(fs);
var md = pe.GetMetadataReader();
var prov = new NameProvider(md);
foreach (var th in md.TypeDefinitions)
{
    var t = md.GetTypeDefinition(th);
    var name = md.GetString(t.Name);
    var ns = md.GetString(t.Namespace);
    var full = string.IsNullOrEmpty(ns) ? name : ns + "." + name;
    if (patterns.Length > 0 && !patterns.Any(p => full.ToLowerInvariant().Contains(p))) continue;
    string baseName = "";
    if (!t.BaseType.IsNil && t.BaseType.Kind == HandleKind.TypeReference)
        baseName = md.GetString(md.GetTypeReference((TypeReferenceHandle)t.BaseType).Name);
    else if (!t.BaseType.IsNil && t.BaseType.Kind == HandleKind.TypeDefinition)
        baseName = md.GetString(md.GetTypeDefinition((TypeDefinitionHandle)t.BaseType).Name);
    Console.WriteLine($"TYPE {full} : {baseName}");
    foreach (var fh in t.GetFields())
    {
        var f = md.GetFieldDefinition(fh);
        var fn = md.GetString(f.Name);
        if (fn == "value__" || fn.StartsWith("NativeFieldInfoPtr") || fn.StartsWith("NativeMethodInfoPtr")) continue;
        if (baseName == "Enum") { Console.WriteLine($"  enum {fn}"); continue; }
        string ft;
        try { ft = f.DecodeSignature(prov, null); } catch { ft = "?"; }
        Console.WriteLine($"  field {ft} {fn}");
    }
    foreach (var ph in t.GetProperties())
    {
        var p = md.GetPropertyDefinition(ph);
        string pt;
        try { pt = p.DecodeSignature(prov, null).ReturnType; } catch { pt = "?"; }
        Console.WriteLine($"  prop {pt} {md.GetString(p.Name)}");
    }
    foreach (var mh in t.GetMethods())
    {
        var m = md.GetMethodDefinition(mh);
        var mn = md.GetString(m.Name);
        if (mn.StartsWith("get_") || mn.StartsWith("set_") || mn == ".ctor" || mn == ".cctor") continue;
        string sig;
        try
        {
            var s = m.DecodeSignature(prov, null);
            sig = $"{s.ReturnType} {mn}({string.Join(", ", s.ParameterTypes)})";
        }
        catch { sig = mn; }
        Console.WriteLine($"  meth {sig}");
    }
}

sealed class NameProvider : ISignatureTypeProvider<string, object>
{
    readonly MetadataReader _md;
    public NameProvider(MetadataReader md) { _md = md; }
    public string GetPrimitiveType(PrimitiveTypeCode c) => c.ToString().ToLowerInvariant();
    public string GetTypeFromDefinition(MetadataReader r, TypeDefinitionHandle h, byte k) => r.GetString(r.GetTypeDefinition(h).Name);
    public string GetTypeFromReference(MetadataReader r, TypeReferenceHandle h, byte k) => r.GetString(r.GetTypeReference(h).Name);
    public string GetTypeFromSpecification(MetadataReader r, object ctx, TypeSpecificationHandle h, byte k) => "spec";
    public string GetSZArrayType(string e) => e + "[]";
    public string GetArrayType(string e, ArrayShape s) => e + "[,]";
    public string GetByReferenceType(string e) => "ref " + e;
    public string GetPointerType(string e) => e + "*";
    public string GetGenericInstantiation(string g, ImmutableArray<string> a) => g.Split('`')[0] + "<" + string.Join(",", a) + ">";
    public string GetGenericTypeParameter(object ctx, int i) => "T" + i;
    public string GetGenericMethodParameter(object ctx, int i) => "M" + i;
    public string GetFunctionPointerType(MethodSignature<string> s) => "fnptr";
    public string GetModifiedType(string m, string u, bool req) => u;
    public string GetPinnedType(string e) => e;
}

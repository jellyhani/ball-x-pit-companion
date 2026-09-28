// 개발용: BepInEx 가 만든 interop 어셈블리에서 타입·필드·속성(형식 포함)·메서드 이름을 뽑는다.
// 어셈블리를 실행·로드하지 않고 메타데이터만 읽는다.
//   InteropDump.exe <dll> [이름 일부...]
using System.Collections.Immutable;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;

var assemblyPath = args[0];
var patterns = args.Skip(1).Select(pattern => pattern.ToLowerInvariant()).ToArray();
using var assemblyStream = File.OpenRead(assemblyPath);
using var assemblyReader = new PEReader(assemblyStream);
var metadataReader = assemblyReader.GetMetadataReader();
var typeNameProvider = new NameProvider(metadataReader);
foreach (var typeHandle in metadataReader.TypeDefinitions)
{
    var typeDefinition = metadataReader.GetTypeDefinition(typeHandle);
    var typeName = metadataReader.GetString(typeDefinition.Name);
    var namespaceName = metadataReader.GetString(typeDefinition.Namespace);
    var fullName = string.IsNullOrEmpty(namespaceName) ? typeName : namespaceName + "." + typeName;
    if (patterns.Length > 0 && !patterns.Any(pattern => fullName.ToLowerInvariant().Contains(pattern))) continue;
    string baseName = "";
    if (!typeDefinition.BaseType.IsNil && typeDefinition.BaseType.Kind == HandleKind.TypeReference)
        baseName = metadataReader.GetString(metadataReader.GetTypeReference((TypeReferenceHandle)typeDefinition.BaseType).Name);
    else if (!typeDefinition.BaseType.IsNil && typeDefinition.BaseType.Kind == HandleKind.TypeDefinition)
        baseName = metadataReader.GetString(metadataReader.GetTypeDefinition((TypeDefinitionHandle)typeDefinition.BaseType).Name);
    Console.WriteLine($"TYPE {fullName} : {baseName}");
    foreach (var fieldHandle in typeDefinition.GetFields())
    {
        var field = metadataReader.GetFieldDefinition(fieldHandle);
        var fieldName = metadataReader.GetString(field.Name);
        if (fieldName == "value__" || fieldName.StartsWith("NativeFieldInfoPtr") || fieldName.StartsWith("NativeMethodInfoPtr")) continue;
        if (baseName == "Enum") { Console.WriteLine($"  enum {fieldName}"); continue; }
        string fieldType;
        try { fieldType = field.DecodeSignature(typeNameProvider, null); } catch { fieldType = "?"; }
        Console.WriteLine($"  field {fieldType} {fieldName}");
    }
    foreach (var propertyHandle in typeDefinition.GetProperties())
    {
        var property = metadataReader.GetPropertyDefinition(propertyHandle);
        string propertyType;
        try { propertyType = property.DecodeSignature(typeNameProvider, null).ReturnType; } catch { propertyType = "?"; }
        Console.WriteLine($"  prop {propertyType} {metadataReader.GetString(property.Name)}");
    }
    foreach (var methodHandle in typeDefinition.GetMethods())
    {
        var method = metadataReader.GetMethodDefinition(methodHandle);
        var methodName = metadataReader.GetString(method.Name);
        if (methodName.StartsWith("get_") || methodName.StartsWith("set_") || methodName == ".ctor" || methodName == ".cctor") continue;
        string signatureText;
        try
        {
            var signature = method.DecodeSignature(typeNameProvider, null);
            signatureText = $"{signature.ReturnType} {methodName}({string.Join(", ", signature.ParameterTypes)})";
        }
        catch { signatureText = methodName; }
        Console.WriteLine($"  meth {signatureText}");
    }
}

sealed class NameProvider : ISignatureTypeProvider<string, object>
{
    readonly MetadataReader _metadataReader;
    public NameProvider(MetadataReader metadataReader) { _metadataReader = metadataReader; }
    public string GetPrimitiveType(PrimitiveTypeCode typeCode) => typeCode.ToString().ToLowerInvariant();
    public string GetTypeFromDefinition(MetadataReader reader, TypeDefinitionHandle handle, byte rawTypeKind) => reader.GetString(reader.GetTypeDefinition(handle).Name);
    public string GetTypeFromReference(MetadataReader reader, TypeReferenceHandle handle, byte rawTypeKind) => reader.GetString(reader.GetTypeReference(handle).Name);
    public string GetTypeFromSpecification(MetadataReader reader, object genericContext, TypeSpecificationHandle handle, byte rawTypeKind) => "spec";
    public string GetSZArrayType(string elementType) => elementType + "[]";
    public string GetArrayType(string elementType, ArrayShape shape) => elementType + "[,]";
    public string GetByReferenceType(string elementType) => "ref " + elementType;
    public string GetPointerType(string elementType) => elementType + "*";
    public string GetGenericInstantiation(string genericType, ImmutableArray<string> typeArguments) => genericType.Split('`')[0] + "<" + string.Join(",", typeArguments) + ">";
    public string GetGenericTypeParameter(object genericContext, int index) => "T" + index;
    public string GetGenericMethodParameter(object genericContext, int index) => "M" + index;
    public string GetFunctionPointerType(MethodSignature<string> signature) => "fnptr";
    public string GetModifiedType(string modifierType, string unmodifiedType, bool isRequired) => unmodifiedType;
    public string GetPinnedType(string elementType) => elementType;
}

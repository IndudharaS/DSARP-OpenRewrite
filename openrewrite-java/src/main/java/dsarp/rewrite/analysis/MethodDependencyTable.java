package dsarp.rewrite.analysis;

import org.openrewrite.Column;
import org.openrewrite.DataTable;
import org.openrewrite.Recipe;

public final class MethodDependencyTable extends DataTable<MethodDependencyTable.Row> {
    public MethodDependencyTable(Recipe recipe) {
        super(recipe, "DSARP method dependencies", "Resolved method-level type dependencies used for candidate selection.");
    }

    public record Row(
            @Column(displayName = "Source class", description = "Fully qualified declaring type") String sourceClass,
            @Column(displayName = "Source method", description = "Simple method name") String sourceMethod,
            @Column(displayName = "Signature", description = "Name and fully qualified parameter types") String signature,
            @Column(displayName = "Source package", description = "Declaring package") String sourcePackage,
            @Column(displayName = "Path", description = "Source path") String path,
            @Column(displayName = "Visibility", description = "Java visibility") String visibility,
            @Column(displayName = "Static", description = "Whether the method is static") boolean isStatic,
            @Column(displayName = "Abstract", description = "Whether the method is abstract") boolean isAbstract,
            @Column(displayName = "Native", description = "Whether the method is native") boolean isNative,
            @Column(displayName = "Synchronized", description = "Whether the method is synchronized") boolean isSynchronized,
            @Column(displayName = "Return type", description = "Resolved return type") String returnType,
            @Column(displayName = "Target type", description = "Resolved dependency type") String targetType,
            @Column(displayName = "Target package", description = "Dependency package") String targetPackage,
            @Column(displayName = "Kind", description = "Dependency kind") String kind,
            @Column(displayName = "Source state", description = "Whether this dependency targets source-class state") boolean sourceState
    ) {}
}

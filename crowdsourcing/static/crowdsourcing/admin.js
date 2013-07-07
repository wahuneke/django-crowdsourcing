// replace all help text <p> with <img> of question mark (a nicer and more compact way to show help text)
$(function() {
    $("div p.help").replaceWith(function () {
        var helptext = $(this).text();
        return "<img src='/static/admin/img/icon-unknown.gif' class='help help-tooltip' width='10' height='10' alt='"+helptext+"' title='"+helptext+"'/>";
    });
});

// use tag-it to allow easy entry of survey question names
$(function() {
    $('div.field-fieldnames input').tagit({
        showAutocompleteOnFocus: true,
        allowSpaces: true,
        tagSource: function(dummy, response) {
            response(all_survey_fieldnames);
        }
    });
});

// right away, we make an ajax call for the full list of survey fields.
// use that one list for the rest of the time this page is open
var all_survey_fieldnames = [];
$(function() {
    $.ajax({
        url: "/survey/api/v1/survey/",
        dataType: "json",
        success: function(data) {
            all_survey_fieldnames = convert_api_to_fieldsnames(data);
        }
    });
});

// function to convert results from successful call to api/v1/survey/ into a simple list of survey.question
// strings
function convert_api_to_fieldsnames(data)
{
    var surveys = data.objects,
        result = [];
    $.each(surveys, function(idx, item) {
        var survey_slug = item.slug;
        $.each(item.questions, function(idx,question) {
            var val = survey_slug+"."+question.fieldname,
                text = item.title + " - " + question.question;
            result.push({label: text, value: val});
        });

    });
    return result;
}
